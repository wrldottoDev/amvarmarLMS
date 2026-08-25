"""Notificaciones: bandeja in-app y correo (Paso 4.2).

Alcance según ADR-0008: `IN_APP` y `EMAIL`. El correo se prueba contra Mailpit
real, no contra un doble: lo que puede romperse es el diálogo SMTP y el armado
del multipart, y un doble que acepta cualquier cosa no lo detecta.
"""

import uuid

import httpx
import pytest
from scripts.seed_rbac import sembrar as sembrar_rbac
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications import email as correo
from app.modules.notifications import service
from app.modules.notifications.catalog import EVENTOS, definicion
from app.modules.notifications.models import Channel, DeliveryStatus
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


class TestCatalogo:
    def test_los_ocho_criticos_de_adr_0008_estan(self) -> None:
        criticos = {c for c, e in EVENTOS.items() if e.critico}
        assert len(criticos) == 8

    def test_ningun_texto_lleva_datos_de_negocio(self) -> None:
        """Regla dura de ADR-0008: el correo no dice qué carga ni qué documento.

        Se comprueba que ningún texto tenga marcadores de sustitución: si los
        tuviera, alguien podría rellenarlos con el número de carga y ese dato
        terminaría en una bandeja de entrada ajena.
        """
        for evento in EVENTOS.values():
            assert "{" not in evento.asunto
            assert "{" not in evento.mensaje


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
