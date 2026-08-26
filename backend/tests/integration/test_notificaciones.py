"""Notificaciones: bandeja in-app y correo (Paso 4.2).

Alcance según ADR-0008: `IN_APP` y `EMAIL`. El correo se prueba contra Mailpit
real, no contra un doble: lo que puede romperse es el diálogo SMTP y el armado
del multipart, y un doble que acepta cualquier cosa no lo detecta.
"""

import uuid

import httpx
import pytest
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin import service as admin_service
from app.modules.audit.outbox import EventoPendiente
from app.modules.notifications import email as correo
from app.modules.notifications import handlers, service
from app.modules.notifications.catalog import EVENTOS, definicion
from app.modules.notifications.catalog import texto as catalogo_texto
from app.modules.notifications.models import Channel, DeliveryStatus
from app.modules.rbac.models import RoleCode
from app.modules.rbac.service import obtener_permisos_efectivos
from app.modules.shipments import service as cargas
from app.modules.shipments.models import ShipmentStatus
from tests.integration.test_requisitos_documentales import _carga, _entorno, _permisos

pytestmark = pytest.mark.integration


async def _empresa(session: AsyncSession) -> uuid.UUID:
    return (
        await session.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"),
            {"n": f"Notif {uuid.uuid4().hex[:6]} S.A."},
        )
    ).scalar_one()


async def _usuario(
    session: AsyncSession, empresa: uuid.UUID | None, *, verificado: bool = True
) -> tuple[uuid.UUID, str]:
    email = f"n-{uuid.uuid4().hex[:10]}@amvarmar.test"
    user_id = (
        await session.execute(
            text("""
                INSERT INTO users
                    (email, password_hash, first_name, last_name, status, email_verified_at)
                VALUES (:e,'h','N','A','ACTIVE', CASE WHEN :v THEN now() ELSE NULL END)
                RETURNING id
            """),
            {"e": email, "v": verificado},
        )
    ).scalar_one()

    if empresa is not None:
        await session.execute(
            text(
                "INSERT INTO company_memberships (company_id, user_id, status) "
                "VALUES (:c,:u,'ACTIVE')"
            ),
            {"c": empresa, "u": user_id},
        )

    return user_id, email


async def _destinatario(
    session: AsyncSession, empresa: uuid.UUID, *, verificado: bool = True
) -> service.Destinatario:
    user_id, email = await _usuario(session, empresa, verificado=verificado)
    return service.Destinatario(
        user_id=user_id, email=email, email_verificado=verificado, company_id=empresa
    )


async def _staff(session: AsyncSession, rol: str) -> uuid.UUID:
    """Personal interno: sin membresía de empresa, con rol global (ADR-0011)."""
    user_id, _ = await _usuario(session, None)
    await session.execute(
        text("""
            INSERT INTO user_role_assignments (user_id, role_id, scope_type)
            SELECT :u, r.id, 'GLOBAL' FROM roles r WHERE r.code = :rol
        """),
        {"u": user_id, "rol": rol},
    )
    return user_id


async def _solicitud(
    session: AsyncSession, empresa: uuid.UUID, usuario: uuid.UUID
) -> tuple[uuid.UUID, str]:
    fila = (
        await session.execute(
            text("""
                INSERT INTO dispatch_requests (company_id, requested_by, method, status)
                VALUES (:c, :u, 'SEA', 'PENDING')
                RETURNING id, dispatch_number
            """),
            {"c": empresa, "u": usuario},
        )
    ).one()
    return fila.id, fila.dispatch_number


async def _carga_con_referencia(
    session: AsyncSession,
    empresa: uuid.UUID,
    valor: str | None,
    *,
    tipo: str = "WR",
    creador: uuid.UUID | None = None,
) -> uuid.UUID:
    """Una carga con su WR o su factura, o sin ninguno de los dos.

    Se inserta directo y no por el servicio: lo que se prueba es de dónde saca
    el manejador el identificador, no el alta de cargas.
    """
    await sembrar_estados(session)

    ubicacion = (
        await session.execute(
            text("""
                INSERT INTO locations (country_code, city_code, location_code, name)
                VALUES ('PA', :ciudad, :codigo, 'Prueba') RETURNING id
            """),
            {
                "ciudad": uuid.uuid4().hex[:3].upper(),
                "codigo": uuid.uuid4().hex[:3].upper(),
            },
        )
    ).scalar_one()

    shipment_id = (
        await session.execute(
            text("""
                INSERT INTO shipments
                    (company_id, created_by, origin_location_id, destination_location_id,
                     transport_mode, current_status_code)
                VALUES (:c, :autor, :l, :l, 'SEA', :estado)
                RETURNING id
            """),
            {
                "c": empresa,
                "autor": creador,
                "l": ubicacion,
                "estado": ShipmentStatus.STORED.value,
            },
        )
    ).scalar_one()

    if valor is not None:
        await session.execute(
            text("""
                INSERT INTO shipment_references (shipment_id, reference_type, value)
                VALUES (:s, :t, :v)
            """),
            {"s": shipment_id, "t": tipo, "v": valor},
        )

    return shipment_id


def _evento_de_outbox(*, aggregate_id: uuid.UUID, payload: dict) -> EventoPendiente:
    """Lo que el worker le pasa a un manejador, sin pasar por el worker."""
    return EventoPendiente(
        id=uuid.uuid4(),
        aggregate_type="prueba",
        aggregate_id=aggregate_id,
        event_type="prueba",
        payload=payload,
        attempt_count=0,
    )


async def _aviso(session: AsyncSession, user_id: uuid.UUID) -> tuple[str, str]:
    """El único aviso de esa persona. Falla si hay más de uno: sería el bug."""
    fila = (
        await session.execute(
            text("SELECT event_code, body FROM notifications WHERE user_id = :u"),
            {"u": user_id},
        )
    ).one()
    return fila.event_code, fila.body


async def _codigo_de_aviso(session: AsyncSession, user_id: uuid.UUID) -> str:
    return (await _aviso(session, user_id))[0]


async def _cuerpo_de_aviso(session: AsyncSession, user_id: uuid.UUID) -> str:
    return (await _aviso(session, user_id))[1]


async def _entregas(session: AsyncSession, notification_id: uuid.UUID) -> dict[str, str]:
    filas = (
        await session.execute(
            text("""
                SELECT channel, status FROM notification_deliveries
                WHERE notification_id = :n
            """),
            {"n": notification_id},
        )
    ).all()
    return {f.channel: f.status for f in filas}


def _mensajes(api: str) -> list[dict]:
    respuesta = httpx.get(f"{api}/api/v1/messages", timeout=10)
    respuesta.raise_for_status()
    return respuesta.json()["messages"]


def _cuerpo(api: str, mensaje_id: str) -> dict:
    respuesta = httpx.get(f"{api}/api/v1/message/{mensaje_id}", timeout=10)
    respuesta.raise_for_status()
    return respuesta.json()


# Los ocho críticos que ADR-0008 enumeró en su versión original, más los que
# agregó la reconstrucción de los seis correos del sistema anterior. Se listan
# uno por uno en vez de contarlos: un número suelto no dice cuál falta cuando
# el test se pone en rojo, y bajarlo para que pase es demasiado fácil.
_CRITICOS_ESPERADOS = frozenset(
    {
        # ADR-0008 original
        "shipment.requirement_blocking",
        "shipment.requirement_rejected",
        "shipment.permit_review",
        "dispatch.status_changed",
        "shipment.dispatched",
        "shipment.delivered",
        "shipment.corrected",
        "account.security_alert",
        # Los seis del sistema Django
        "shipment.stored",
        "dispatch.requested",
        "dispatch.requested_internal",
        "dispatch.approved",
        "dispatch.bol_available",
        "account.invitation",
        # El correo que `/password/forgot` prometía y no mandaba
        "account.password_reset",
    }
)


class TestCatalogo:
    def test_estan_todos_los_criticos_y_ninguno_de_mas(self) -> None:
        criticos = {c for c, e in EVENTOS.items() if e.critico}
        assert criticos == _CRITICOS_ESPERADOS

    def test_lo_unico_sustituible_es_el_identificador(self) -> None:
        """ADR-0008 enmendado: del negocio solo sale el identificador.

        El asunto y el texto genérico no llevan ningún marcador. La variante
        `con_referencia` lleva `{ref}` y nada más: si apareciera otro hueco,
        alguien podría rellenarlo con el shipper, el carrier o el peso, y eso
        terminaría en una bandeja de entrada ajena.
        """
        for evento in EVENTOS.values():
            assert "{" not in evento.asunto
            assert "{" not in evento.mensaje

            if evento.con_referencia is not None:
                assert evento.con_referencia.replace("{ref}", "").count("{") == 0

    def test_los_seis_del_sistema_anterior_tienen_su_reemplazo(self) -> None:
        """Cada plantilla de `templates/emails/` del Django tiene su evento acá."""
        for codigo in (
            "shipment.stored",  # new_warehouse.html
            "dispatch.requested",  # dispatch_received.html
            "dispatch.requested_internal",  # dispatch_request.html
            "dispatch.approved",  # dispatch_approved.html
            "dispatch.bol_available",  # dispatch_bol.html
            "account.invitation",  # credentials.html
        ):
            assert definicion(codigo) is not None, codigo

    def test_sin_identificador_el_texto_no_queda_con_un_hueco(self) -> None:
        """Las cargas migradas sin WR ni factura existen: no puede salir vacío."""
        evento = definicion("shipment.dispatched")
        assert catalogo_texto(evento, None) == evento.mensaje
        assert "WR105921" in catalogo_texto(evento, "WR105921")


class TestComposicion:
    def test_el_enlace_apunta_al_frontend_y_al_recurso(self, correo_de_prueba: str) -> None:
        evento = definicion("shipment.delivered")
        recurso = str(uuid.uuid4())

        compuesto = correo.componer(evento, resource_id=recurso)

        assert compuesto.enlace == f"https://app.amvarmar.test/cargas/{recurso}"
        assert recurso in compuesto.html
        assert recurso in compuesto.texto

    def test_hay_version_de_plantilla(self) -> None:
        """Sin versión no se puede saber con qué texto salió un correo viejo."""
        assert correo.PLANTILLA_VERSION


class TestEntregaInApp:
    async def test_crear_el_aviso_es_la_entrega(self, session: AsyncSession) -> None:
        """IN_APP no tiene red de por medio: no puede quedar pendiente."""
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        destinatario = await _destinatario(session, empresa, verificado=False)

        creadas = await service.notificar(
            session,
            event_code="shipment.dispatched",
            destinatarios=[destinatario],
            resource_type="shipment",
            resource_id=uuid.uuid4(),
            dedup_key=f"prueba-{uuid.uuid4().hex}",
        )

        assert len(creadas) == 1
        entregas = await _entregas(session, creadas[0])
        assert entregas[Channel.IN_APP] == DeliveryStatus.SENT

    async def test_sin_correo_verificado_el_email_se_salta(self, session: AsyncSession) -> None:
        """SKIPPED, no FAILED: no es un error que haya que perseguir.

        Mandar a una dirección sin verificar filtraría el aviso a quien haya
        puesto un correo ajeno al registrarse.
        """
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        destinatario = await _destinatario(session, empresa, verificado=False)

        creadas = await service.notificar(
            session,
            event_code="shipment.delivered",
            destinatarios=[destinatario],
            dedup_key=f"prueba-{uuid.uuid4().hex}",
        )

        entregas = await _entregas(session, creadas[0])
        assert entregas[Channel.EMAIL] == DeliveryStatus.SKIPPED

    async def test_avisa_a_todos_los_miembros_de_la_empresa(self, session: AsyncSession) -> None:
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        for _ in range(3):
            await _usuario(session, empresa, verificado=False)

        destinatarios = await service.destinatarios_de_empresa(session, empresa)

        assert len(destinatarios) == 3

    async def test_un_usuario_inactivo_no_recibe(self, session: AsyncSession) -> None:
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        user_id, _ = await _usuario(session, empresa)
        await session.execute(
            text("UPDATE users SET status = 'SUSPENDED' WHERE id = :u"), {"u": user_id}
        )

        assert await service.destinatarios_de_empresa(session, empresa) == []

    async def test_evento_fuera_del_catalogo_se_rechaza(self, session: AsyncSession) -> None:
        with pytest.raises(service.EventoDesconocido):
            await service.notificar(
                session,
                event_code="inventado.no_existe",
                destinatarios=[],
                dedup_key="x",
            )


class TestIdempotencia:
    async def test_procesar_dos_veces_no_duplica_el_aviso(self, session: AsyncSession) -> None:
        """El outbox entrega al menos una vez (ADR-0014): esto lo absorbe."""
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        destinatario = await _destinatario(session, empresa, verificado=False)
        clave = f"prueba-{uuid.uuid4().hex}"

        primera = await service.notificar(
            session,
            event_code="shipment.delivered",
            destinatarios=[destinatario],
            dedup_key=clave,
        )
        segunda = await service.notificar(
            session,
            event_code="shipment.delivered",
            destinatarios=[destinatario],
            dedup_key=clave,
        )

        assert len(primera) == 1
        assert segunda == []

        total = (
            await session.execute(
                text("SELECT count(*) FROM notifications WHERE user_id = :u"),
                {"u": destinatario.user_id},
            )
        ).scalar_one()
        assert total == 1

    async def test_dos_hechos_distintos_generan_dos_avisos(self, session: AsyncSession) -> None:
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        destinatario = await _destinatario(session, empresa, verificado=False)

        for _ in range(2):
            await service.notificar(
                session,
                event_code="shipment.status_changed",
                destinatarios=[destinatario],
                dedup_key=f"prueba-{uuid.uuid4().hex}",
            )

        total = (
            await session.execute(
                text("SELECT count(*) FROM notifications WHERE user_id = :u"),
                {"u": destinatario.user_id},
            )
        ).scalar_one()
        assert total == 2


class TestBandeja:
    async def _sembrar(self, session: AsyncSession, cantidad: int):
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        destinatario = await _destinatario(session, empresa, verificado=False)

        for _ in range(cantidad):
            await service.notificar(
                session,
                event_code="shipment.status_changed",
                destinatarios=[destinatario],
                dedup_key=f"prueba-{uuid.uuid4().hex}",
            )

        return destinatario

    async def test_lista_lo_mas_reciente_primero(self, session: AsyncSession) -> None:
        destinatario = await self._sembrar(session, 5)

        pagina = await service.listar(session, user_id=destinatario.user_id, limite=25, cursor=None)

        fechas = [n.created_at for n in pagina.items]
        assert fechas == sorted(fechas, reverse=True)
        assert len(pagina.items) == 5

    async def test_pagina_por_cursor_sin_repetir(self, session: AsyncSession) -> None:
        destinatario = await self._sembrar(session, 7)

        primera = await service.listar(session, user_id=destinatario.user_id, limite=3, cursor=None)
        assert primera.has_more

        from app.core.pagination import Cursor

        segunda = await service.listar(
            session,
            user_id=destinatario.user_id,
            limite=3,
            cursor=Cursor.decodificar(primera.next_cursor),
        )

        assert {n.id for n in primera.items} & {n.id for n in segunda.items} == set()

    async def test_marcar_leida_baja_el_contador(self, session: AsyncSession) -> None:
        destinatario = await self._sembrar(session, 3)
        assert await service.contar_no_leidas(session, destinatario.user_id) == 3

        pagina = await service.listar(session, user_id=destinatario.user_id, limite=25, cursor=None)
        assert await service.marcar_leida(
            session, notification_id=pagina.items[0].id, user_id=destinatario.user_id
        )

        assert await service.contar_no_leidas(session, destinatario.user_id) == 2

    async def test_no_se_marca_la_notificacion_de_otro(self, session: AsyncSession) -> None:
        """El filtro va en el WHERE: traerla y descartarla ya la habría expuesto."""
        destinatario = await self._sembrar(session, 1)
        otro = await self._sembrar(session, 1)

        pagina = await service.listar(session, user_id=destinatario.user_id, limite=25, cursor=None)

        assert not await service.marcar_leida(
            session, notification_id=pagina.items[0].id, user_id=otro.user_id
        )
        assert await service.contar_no_leidas(session, destinatario.user_id) == 1

    async def test_marcar_todas_no_toca_las_ya_leidas(self, session: AsyncSession) -> None:
        destinatario = await self._sembrar(session, 4)
        pagina = await service.listar(session, user_id=destinatario.user_id, limite=25, cursor=None)
        await service.marcar_leida(
            session, notification_id=pagina.items[0].id, user_id=destinatario.user_id
        )

        marcadas = await service.marcar_todas_leidas(session, destinatario.user_id)

        assert marcadas == 3
        assert await service.contar_no_leidas(session, destinatario.user_id) == 0

    async def test_filtro_de_no_leidas(self, session: AsyncSession) -> None:
        destinatario = await self._sembrar(session, 3)
        pagina = await service.listar(session, user_id=destinatario.user_id, limite=25, cursor=None)
        await service.marcar_leida(
            session, notification_id=pagina.items[0].id, user_id=destinatario.user_id
        )

        solo_nuevas = await service.listar(
            session,
            user_id=destinatario.user_id,
            limite=25,
            cursor=None,
            solo_no_leidas=True,
        )

        assert len(solo_nuevas.items) == 2


class TestSeisCorreosDelSistemaAnterior:
    """Los seis correos del Django, reconstruidos (ver la enmienda de ADR-0008)."""

    async def test_la_creacion_de_un_despacho_avisa_al_cliente_y_a_operaciones(
        self, session: AsyncSession
    ) -> None:
        """Un solo hecho, dos avisos distintos, con destinatarios distintos.

        En el sistema anterior eran dos llamadas sueltas desde la vista y podía
        salir una sin la otra. Acá salen del mismo evento del outbox.
        """
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        cliente, _ = await _usuario(session, empresa)
        interno = await _staff(session, "OPS_ADMIN")

        dispatch_id, numero = await _solicitud(session, empresa, cliente)

        await handlers.solicitud_de_despacho_creada(
            session,
            _evento_de_outbox(
                aggregate_id=dispatch_id,
                payload={"company_id": str(empresa), "hacia": "PENDING"},
            ),
        )

        assert await _codigo_de_aviso(session, cliente) == "dispatch.requested"
        assert await _codigo_de_aviso(session, interno) == "dispatch.requested_internal"

        # El número de solicitud es lo único de negocio que sale; sin él el
        # correo no dice de qué despacho habla.
        assert numero in await _cuerpo_de_aviso(session, cliente)

    async def test_operaciones_no_recibe_el_aviso_bajo_la_empresa_del_cliente(
        self, session: AsyncSession
    ) -> None:
        """El aviso interno no pertenece a la empresa que lo originó.

        Si llevara su `company_id`, aparecería filtrado bajo esa empresa en la
        bandeja del staff, como si fuera un aviso del cliente y no un pendiente
        propio.
        """
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        cliente, _ = await _usuario(session, empresa)
        interno = await _staff(session, "SUPER_ADMIN")

        dispatch_id, _ = await _solicitud(session, empresa, cliente)
        await handlers.solicitud_de_despacho_creada(
            session,
            _evento_de_outbox(
                aggregate_id=dispatch_id,
                payload={"company_id": str(empresa), "hacia": "PENDING"},
            ),
        )

        fila = (
            await session.execute(
                text("SELECT company_id FROM notifications WHERE user_id = :u"), {"u": interno}
            )
        ).scalar_one()
        assert fila is None

    async def test_un_ops_agent_no_recibe_el_pedido_de_aprobacion(
        self, session: AsyncSession
    ) -> None:
        """No puede aprobar: llenarle la bandeja termina en que nadie lee nada."""
        await sembrar_rbac(session)
        agente = await _staff(session, "OPS_AGENT")

        destinatarios = await service.destinatarios_de_operaciones(session)

        assert agente not in {d.user_id for d in destinatarios}

    async def test_almacenar_una_carga_avisa_con_su_identificador(
        self, session: AsyncSession
    ) -> None:
        """Reemplaza `new_warehouse.html`: la mercadería llegó y está contada."""
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        usuario, _ = await _usuario(session, empresa)
        shipment_id = await _carga_con_referencia(session, empresa, "WR105921", creador=usuario)

        await handlers.cambio_de_estado_de_carga(
            session,
            _evento_de_outbox(
                aggregate_id=shipment_id,
                payload={"desde": ShipmentStatus.RECEIVED, "hacia": ShipmentStatus.STORED},
            ),
        )

        assert await _codigo_de_aviso(session, usuario) == "shipment.stored"
        assert "WR105921" in await _cuerpo_de_aviso(session, usuario)

    async def test_una_carga_sin_wr_ni_factura_avisa_igual(self, session: AsyncSession) -> None:
        """Las cargas migradas sin identificador existen y no pueden romper esto."""
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        usuario, _ = await _usuario(session, empresa)
        shipment_id = await _carga_con_referencia(session, empresa, None, creador=usuario)

        await handlers.cambio_de_estado_de_carga(
            session,
            _evento_de_outbox(
                aggregate_id=shipment_id,
                payload={"desde": ShipmentStatus.RECEIVED, "hacia": ShipmentStatus.STORED},
            ),
        )

        cuerpo = await _cuerpo_de_aviso(session, usuario)
        assert "{" not in cuerpo
        assert cuerpo.strip()

    async def test_la_factura_sirve_de_identificador_cuando_no_hay_wr(
        self, session: AsyncSession
    ) -> None:
        """Solo lo que sale de una bodega que emite WR lo lleva; el resto, factura."""
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        usuario, _ = await _usuario(session, empresa)
        shipment_id = await _carga_con_referencia(
            session, empresa, "F-2026-0088", tipo="INVOICE", creador=usuario
        )

        await handlers.cambio_de_estado_de_carga(
            session,
            _evento_de_outbox(
                aggregate_id=shipment_id,
                payload={"desde": ShipmentStatus.STORED, "hacia": ShipmentStatus.DISPATCHED},
            ),
        )

        assert "F-2026-0088" in await _cuerpo_de_aviso(session, usuario)

    async def test_completar_un_despacho_avisa_del_bill_of_lading(
        self, session: AsyncSession
    ) -> None:
        """Reemplaza `dispatch_bol.html`, que adjuntaba los PDF al correo."""
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        usuario, _ = await _usuario(session, empresa)
        dispatch_id, _ = await _solicitud(session, empresa, usuario)

        await handlers.cambio_de_estado_de_despacho(
            session,
            _evento_de_outbox(
                aggregate_id=dispatch_id,
                payload={"company_id": str(empresa), "desde": "PREPARING", "hacia": "COMPLETED"},
            ),
        )

        assert await _codigo_de_aviso(session, usuario) == "dispatch.bol_available"

    async def test_aprobar_un_despacho_tiene_su_propio_aviso(self, session: AsyncSession) -> None:
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        usuario, _ = await _usuario(session, empresa)
        dispatch_id, _ = await _solicitud(session, empresa, usuario)

        await handlers.cambio_de_estado_de_despacho(
            session,
            _evento_de_outbox(
                aggregate_id=dispatch_id,
                payload={"company_id": str(empresa), "desde": "PENDING", "hacia": "APPROVED"},
            ),
        )

        assert await _codigo_de_aviso(session, usuario) == "dispatch.approved"

    async def test_un_estado_sin_aviso_propio_cae_en_el_generico(
        self, session: AsyncSession
    ) -> None:
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        usuario, _ = await _usuario(session, empresa)
        dispatch_id, _ = await _solicitud(session, empresa, usuario)

        await handlers.cambio_de_estado_de_despacho(
            session,
            _evento_de_outbox(
                aggregate_id=dispatch_id,
                payload={"company_id": str(empresa), "desde": "APPROVED", "hacia": "PREPARING"},
            ),
        )

        assert await _codigo_de_aviso(session, usuario) == "dispatch.status_changed"


@pytest.mark.slow
class TestCorreoReal:
    """Contra Mailpit de verdad. Marcado `slow`: levanta un contenedor."""

    async def test_el_correo_llega_con_texto_y_html(
        self, session: AsyncSession, correo_de_prueba: str
    ) -> None:
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        destinatario = await _destinatario(session, empresa)
        recurso = uuid.uuid4()

        creadas = await service.notificar(
            session,
            event_code="shipment.delivered",
            destinatarios=[destinatario],
            resource_type="shipment",
            resource_id=recurso,
            dedup_key=f"prueba-{uuid.uuid4().hex}",
        )

        mensajes = _mensajes(correo_de_prueba)
        assert len(mensajes) == 1
        assert mensajes[0]["To"][0]["Address"] == destinatario.email
        assert mensajes[0]["Subject"] == "Su carga fue entregada"

        cuerpo = _cuerpo(correo_de_prueba, mensajes[0]["ID"])
        # Multipart: los clientes que no renderizan HTML igual leen el aviso.
        assert cuerpo["Text"].strip()
        assert cuerpo["HTML"].strip()
        assert f"https://app.amvarmar.test/cargas/{recurso}" in cuerpo["Text"]

        assert (await _entregas(session, creadas[0]))[Channel.EMAIL] == DeliveryStatus.SENT

    async def test_el_correo_no_lleva_datos_de_negocio_ni_adjuntos(
        self, session: AsyncSession, correo_de_prueba: str
    ) -> None:
        """ADR-0008. Es la prueba que impide que alguien "mejore" la plantilla.

        Un correo queda en la bandeja de entrada, se reenvía y se sincroniza a
        servicios de terceros. El dato concreto vive detrás del login.
        """
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        destinatario = await _destinatario(session, empresa)

        # Una carga con número reconocible: si la plantilla lo filtrara, saldría.
        numero = f"AMV-{uuid.uuid4().hex[:8].upper()}"
        await session.execute(
            text("UPDATE companies SET legal_name = :n WHERE id = :c"),
            {"n": numero, "c": empresa},
        )

        await service.notificar(
            session,
            event_code="shipment.requirement_blocking",
            destinatarios=[destinatario],
            resource_type="shipment",
            resource_id=uuid.uuid4(),
            dedup_key=f"prueba-{uuid.uuid4().hex}",
        )

        cuerpo = _cuerpo(correo_de_prueba, _mensajes(correo_de_prueba)[0]["ID"])

        assert numero not in cuerpo["Text"]
        assert numero not in cuerpo["HTML"]
        assert cuerpo["Attachments"] == []

    async def test_el_identificador_sale_pero_el_detalle_comercial_no(
        self, session: AsyncSession, correo_de_prueba: str
    ) -> None:
        """El corte de la enmienda de ADR-0008, contra un buzón real.

        El WR ya está en los papeles del embarque y el cliente lo tiene, así
        que repetirlo no expone nada nuevo. El shipper y el carrier son la
        relación comercial, y de esos sí se aprende algo mirando un buzón ajeno.
        """
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        destinatario = await _destinatario(session, empresa)

        await service.notificar(
            session,
            event_code="shipment.stored",
            destinatarios=[destinatario],
            resource_type="shipment",
            resource_id=uuid.uuid4(),
            referencia="WR105921",
            dedup_key=f"prueba-{uuid.uuid4().hex}",
        )

        mensaje = _mensajes(correo_de_prueba)[0]
        cuerpo = _cuerpo(correo_de_prueba, mensaje["ID"])

        # El asunto también lo lleva: veinte asuntos idénticos en una bandeja no
        # dicen cuál es cuál.
        assert "WR105921" in mensaje["Subject"]
        assert "WR105921" in cuerpo["Text"]
        assert "WR105921" in cuerpo["HTML"]
        assert cuerpo["Attachments"] == []

    async def test_la_invitacion_llega_con_enlace_y_sin_contrasena(
        self, session: AsyncSession, correo_de_prueba: str, redis
    ) -> None:
        """Lo que separa esto de `credentials.html` del sistema anterior.

        Aquel mandaba usuario y contraseña en texto plano, que quedan para
        siempre en ese buzón y en el de quien lo reenvíe. Acá viaja un enlace
        de un solo uso y la contraseña la elige la persona.
        """
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        admin = await _staff(session, "SUPER_ADMIN")
        await session.commit()

        creado = await admin_service.crear_usuario(
            session,
            email=f"alta-{uuid.uuid4().hex[:8]}@amvarmar.test",
            first_name="Nora",
            last_name="Salas",
            role_code=RoleCode.CLIENT_USER,
            company_id=empresa,
            phone=None,
            permisos=await obtener_permisos_efectivos(session, redis, admin),
        )

        assert creado.invitacion_enviada is True

        cuerpo = _cuerpo(correo_de_prueba, _mensajes(correo_de_prueba)[0]["ID"])

        assert "https://app.amvarmar.test/invitacion/" in cuerpo["Text"]
        # Lo que nunca puede viajar.
        assert creado.password_temporal not in cuerpo["Text"]
        assert creado.password_temporal not in cuerpo["HTML"]
        assert creado.email not in cuerpo["Text"]

    async def test_la_invitacion_sale_aunque_el_correo_no_este_verificado(
        self, session: AsyncSession, correo_de_prueba: str, redis
    ) -> None:
        """Exigir verificación acá sería circular.

        La persona no puede verificar su dirección sin recibir el correo que se
        lo pide. Con la regla general, toda cuenta nueva quedaría sin acceso.
        """
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        admin = await _staff(session, "SUPER_ADMIN")
        await session.commit()

        creado = await admin_service.crear_usuario(
            session,
            email=f"sinverificar-{uuid.uuid4().hex[:8]}@amvarmar.test",
            first_name="Beto",
            last_name="Cruz",
            role_code=RoleCode.CLIENT_USER,
            company_id=empresa,
            phone=None,
            permisos=await obtener_permisos_efectivos(session, redis, admin),
        )

        verificado = (
            await session.execute(
                text("SELECT email_verified_at FROM users WHERE id = :u"), {"u": creado.id}
            )
        ).scalar_one()

        assert verificado is None
        assert len(_mensajes(correo_de_prueba)) == 1

    async def test_un_relay_caido_no_impide_dar_de_alta(
        self, session: AsyncSession, correo_de_prueba: str, redis, monkeypatch
    ) -> None:
        """El alta no puede depender de que el servidor de correo responda.

        Se monta sobre el fixture de Mailpit a propósito: sin él el envío
        fallaría de todas formas por falta de relay y la prueba pasaría sin
        ejercitar nada.
        """
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        admin = await _staff(session, "SUPER_ADMIN")
        await session.commit()

        async def falla(*_args: object, **_kwargs: object) -> None:
            raise correo.EnvioFallido("relay caído")

        monkeypatch.setattr(correo, "enviar", falla)

        creado = await admin_service.crear_usuario(
            session,
            email=f"sinrelay-{uuid.uuid4().hex[:8]}@amvarmar.test",
            first_name="Sara",
            last_name="Lima",
            role_code=RoleCode.CLIENT_USER,
            company_id=empresa,
            phone=None,
            permisos=await obtener_permisos_efectivos(session, redis, admin),
        )

        # La cuenta existe y la temporal sirve para entrar: el administrador
        # tiene cómo darle acceso mientras el relay vuelve.
        assert creado.invitacion_enviada is False
        assert creado.password_temporal
        assert _mensajes(correo_de_prueba) == []

        # Y queda registro de que falló, no un silencio.
        estado = (
            await session.execute(
                text("""
                    SELECT d.status FROM notification_deliveries d
                    JOIN notifications n ON n.id = d.notification_id
                    WHERE n.user_id = :u AND d.channel = 'EMAIL'
                """),
                {"u": creado.id},
            )
        ).scalar_one()
        assert estado == DeliveryStatus.FAILED

    async def test_un_reproceso_no_manda_dos_correos(
        self, session: AsyncSession, correo_de_prueba: str
    ) -> None:
        """El gate del canal: el outbox reenvía y la persona no recibe doble."""
        await sembrar_rbac(session)
        empresa = await _empresa(session)
        destinatario = await _destinatario(session, empresa)
        clave = f"prueba-{uuid.uuid4().hex}"

        for _ in range(3):
            await service.notificar(
                session,
                event_code="shipment.dispatched",
                destinatarios=[destinatario],
                resource_id=uuid.uuid4(),
                dedup_key=clave,
            )

        assert len(_mensajes(correo_de_prueba)) == 1

    async def test_un_relay_caido_deja_la_entrega_en_failed(
        self, session: AsyncSession, correo_de_prueba: str
    ) -> None:
        """Y se relanza, para que el outbox reintente con backoff."""
        import os

        from app.core.config import get_settings

        await sembrar_rbac(session)
        empresa = await _empresa(session)
        destinatario = await _destinatario(session, empresa)

        # Puerto sin nada escuchando: el relay está caído.
        os.environ["SMTP_PORT"] = "1"
        get_settings.cache_clear()

        try:
            with pytest.raises(correo.EnvioFallido):
                await service.notificar(
                    session,
                    event_code="shipment.delivered",
                    destinatarios=[destinatario],
                    dedup_key=f"prueba-{uuid.uuid4().hex}",
                )
        finally:
            get_settings.cache_clear()

        fila = (
            await session.execute(
                text("""
                    SELECT d.status, d.last_error, d.target
                    FROM notification_deliveries d
                    JOIN notifications n ON n.id = d.notification_id
                    WHERE n.user_id = :u AND d.channel = 'EMAIL'
                """),
                {"u": destinatario.user_id},
            )
        ).one()

        assert fila.status == DeliveryStatus.FAILED
        assert fila.last_error
        # El destino queda registrado aunque el envío falle: sin esto, "no llegó
        # el correo" no se puede diagnosticar sin acceso al relay.
        assert fila.target == destinatario.email

        # El aviso in-app SÍ llegó: que el correo falle no deja al usuario sin
        # enterarse dentro del sistema.
        assert await service.contar_no_leidas(session, destinatario.user_id) == 1


class TestFlujoDesdeElOutbox:
    """De cambio de negocio a bandeja, pasando por el worker."""

    async def _procesar(self, session: AsyncSession) -> None:
        from app.modules.audit.outbox import procesar_lote
        from app.workers.tasks.outbox import construir_manejador

        await procesar_lote(session, construir_manejador(session), limite=200)

    async def test_una_transicion_termina_en_la_bandeja_del_cliente(
        self, session: AsyncSession, redis
    ) -> None:
        ctx = await _entorno(session)
        cliente = service.Destinatario(
            user_id=ctx["cliente"], email=None, email_verificado=False, company_id=ctx["empresa"]
        )
        carga = await _carga(session, ctx, estado=ShipmentStatus.IN_TRANSIT)

        await cargas.transicionar(
            session,
            shipment_id=carga,
            datos=cargas.DatosTransicion(to_status=ShipmentStatus.RECEIVED, row_version=1),
            actor_user_id=ctx["admin"],
            permisos=await _permisos(session, redis, ctx["admin"]),
        )
        await self._procesar(session)

        pagina = await service.listar(session, user_id=cliente.user_id, limite=25, cursor=None)

        assert len(pagina.items) == 1
        assert pagina.items[0].event_code == "shipment.status_changed"
        assert pagina.items[0].resource_id == carga
        assert pagina.items[0].is_critical is False

    async def test_el_despacho_genera_un_aviso_critico(self, session: AsyncSession, redis) -> None:
        """`DISPATCHED` es crítico en ADR-0008, a diferencia del avance normal."""
        ctx = await _entorno(session)
        carga = await _carga(session, ctx, estado=ShipmentStatus.PREPARING)

        await cargas.transicionar(
            session,
            shipment_id=carga,
            datos=cargas.DatosTransicion(to_status=ShipmentStatus.DISPATCHED, row_version=1),
            actor_user_id=ctx["admin"],
            permisos=await _permisos(session, redis, ctx["admin"]),
        )
        await self._procesar(session)

        pagina = await service.listar(session, user_id=ctx["cliente"], limite=25, cursor=None)

        assert pagina.items[0].event_code == "shipment.dispatched"
        assert pagina.items[0].is_critical is True

    async def test_reprocesar_el_lote_no_duplica_la_bandeja(
        self, session: AsyncSession, redis
    ) -> None:
        """La entrega es al menos una vez (ADR-0014); la bandeja no lo refleja."""
        ctx = await _entorno(session)
        carga = await _carga(session, ctx, estado=ShipmentStatus.IN_TRANSIT)

        await cargas.transicionar(
            session,
            shipment_id=carga,
            datos=cargas.DatosTransicion(to_status=ShipmentStatus.RECEIVED, row_version=1),
            actor_user_id=ctx["admin"],
            permisos=await _permisos(session, redis, ctx["admin"]),
        )

        await self._procesar(session)
        # Se devuelve el evento a pendiente, como si el worker hubiera muerto
        # antes de commitear su marcado.
        await session.execute(
            text("""
                UPDATE outbox_events SET status = 'PENDING', processed_at = NULL
                WHERE aggregate_id = :c
            """),
            {"c": carga},
        )
        await self._procesar(session)

        pagina = await service.listar(session, user_id=ctx["cliente"], limite=25, cursor=None)
        assert len(pagina.items) == 1

    async def test_un_evento_sin_manejador_no_se_reintenta(self, session: AsyncSession) -> None:
        from app.modules.audit.outbox import publicar

        evento_id = await publicar(
            session,
            aggregate_type="prueba",
            aggregate_id=uuid.uuid4(),
            event_type="evento.sin_manejador",
            dedup_key=f"sin-manejador-{uuid.uuid4().hex}",
        )

        await self._procesar(session)

        estado = (
            await session.execute(
                text("SELECT status FROM outbox_events WHERE id = :id"), {"id": evento_id}
            )
        ).scalar_one()
        assert estado == "DONE"
