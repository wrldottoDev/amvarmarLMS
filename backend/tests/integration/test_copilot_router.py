"""Endpoints HTTP del asistente (ADR-0012).

Todo pasa por `cliente` (cliente HTTP real contra la app, autenticado por
`/auth/login` como el resto de las pruebas HTTP del proyecto) con el proveedor
de IA reemplazado por `_ProveedorFalso` vía `app.dependency_overrides` —
ninguna prueba de este archivo toca la red.
"""

import asyncio
import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security.argon2 import hash_password
from app.main import app
from app.modules.copilot import executors_escritura
from app.modules.copilot import provider as copilot_provider
from app.modules.copilot.acciones import REGISTRO_DE_CONFIRMACION, AccionCopilot
from app.modules.copilot.provider import RespuestaProveedor
from app.modules.copilot.provider_falso import ProveedorFalsoDeterministico
from app.modules.copilot.router import _fabrica_proveedor
from app.modules.rbac.models import RoleCode, ScopeType
from app.modules.rbac.service import obtener_permisos_efectivos
from tests.piezas import sembrar_pieza

pytestmark = pytest.mark.integration

PASSWORD = "una passphrase de prueba suficientemente larga"


class _ProveedorFalso:
    def __init__(self, guion: list[RespuestaProveedor]):
        self._guion = guion
        self.llamadas = 0

    async def responder(
        self, *, entrada: list[dict[str, Any]], herramientas: list[dict[str, Any]]
    ) -> RespuestaProveedor:
        self.llamadas += 1
        return self._guion[self.llamadas - 1]


def _texto(texto: str) -> RespuestaProveedor:
    return RespuestaProveedor(
        id="resp_1",
        texto=texto,
        llamadas_herramientas=(),
        items_salida=(),
        tokens_entrada=5,
        tokens_salida=5,
    )


@pytest.fixture
def usar_proveedor():
    """Reemplaza el proveedor real por uno falso solo para el test, y limpia
    al terminar aunque el test falle."""

    def _usar(proveedor) -> None:
        app.dependency_overrides[_fabrica_proveedor] = lambda: lambda: proveedor

    yield _usar
    app.dependency_overrides.pop(_fabrica_proveedor, None)


@pytest.fixture(autouse=True)
def _breaker_limpio():
    """El circuit breaker es estado de módulo — sin esto, un test que lo abre
    dejaría el siguiente empezando ya degradado."""
    copilot_provider._reiniciar_breaker_para_pruebas()
    yield
    copilot_provider._reiniciar_breaker_para_pruebas()


async def _usuario_interno(
    session: AsyncSession, rol: str = RoleCode.OPS_ADMIN
) -> tuple[uuid.UUID, str]:
    email = f"copilot-int-{uuid.uuid4().hex[:8]}@amvarmar.com"
    user_id = (
        await session.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e, :h, 'Ana', 'Pérez', 'ACTIVE') RETURNING id
            """),
            {"e": email, "h": hash_password(PASSWORD)},
        )
    ).scalar_one()
    await session.execute(
        text("""
            INSERT INTO user_role_assignments (user_id, role_id, scope_type)
            SELECT :u, r.id, :scope FROM roles r WHERE r.code = :rol
        """),
        {"u": user_id, "rol": rol, "scope": ScopeType.GLOBAL},
    )
    return user_id, email


async def _usuario_cliente(session: AsyncSession) -> tuple[uuid.UUID, str, uuid.UUID]:
    empresa = (
        await session.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"),
            {"n": f"Copiloto {uuid.uuid4().hex[:6]} S.A."},
        )
    ).scalar_one()
    email = f"copilot-cli-{uuid.uuid4().hex[:8]}@amvarmar.com"
    user_id = (
        await session.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e, :h, 'Beto', 'Gómez', 'ACTIVE') RETURNING id
            """),
            {"e": email, "h": hash_password(PASSWORD)},
        )
    ).scalar_one()
    await session.execute(
        text("""
            INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
            SELECT :u, r.id, :scope, :c FROM roles r WHERE r.code = :rol
        """),
        {"u": user_id, "rol": RoleCode.CLIENT_USER, "scope": ScopeType.ORGANIZATION, "c": empresa},
    )
    await session.execute(
        text(
            "INSERT INTO company_memberships (company_id, user_id, status) VALUES (:c,:u,'ACTIVE')"
        ),
        {"c": empresa, "u": user_id},
    )
    return user_id, email, empresa


async def _autenticar(cliente: AsyncClient, email: str) -> dict[str, str]:
    respuesta = await cliente.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {respuesta.json()['access_token']}"}


@contextmanager
def _clave_openai(valor: str | None):
    """Fuerza `openai_api_key` para la duración del bloque, sin tocar la API real.

    `os.environ.pop(...)` NO alcanza para simular "sin clave": `Settings` lee
    `.env` como respaldo, y este entorno de desarrollo tiene una clave real
    ahí. Un valor de env var (incluida la cadena vacía) SÍ tiene prioridad
    sobre `.env` — por eso acá se asigna `""` en vez de quitar la variable.
    Sin este cuidado, "sin clave" termina llamando a la API real por error.
    """
    anterior = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = valor if valor is not None else ""
    get_settings.cache_clear()
    try:
        yield
    finally:
        if anterior is not None:
            os.environ["OPENAI_API_KEY"] = anterior
        else:
            os.environ.pop("OPENAI_API_KEY", None)
        get_settings.cache_clear()


@contextmanager
def _proveedor_falso(activo: bool):
    """Fuerza `COPILOT_PROVEEDOR_FALSO` (y `ENVIRONMENT=local`, que el
    validador de `Settings` exige) para la duración del bloque. Igual que
    `_clave_openai`: un valor de env var real, no un `pop`, porque `Settings`
    cae a `.env` en su ausencia."""
    anterior_flag = os.environ.get("COPILOT_PROVEEDOR_FALSO")
    anterior_entorno = os.environ.get("ENVIRONMENT")
    os.environ["COPILOT_PROVEEDOR_FALSO"] = "true" if activo else "false"
    os.environ["ENVIRONMENT"] = "local"
    get_settings.cache_clear()
    try:
        yield
    finally:
        if anterior_flag is not None:
            os.environ["COPILOT_PROVEEDOR_FALSO"] = anterior_flag
        else:
            os.environ.pop("COPILOT_PROVEEDOR_FALSO", None)
        if anterior_entorno is not None:
            os.environ["ENVIRONMENT"] = anterior_entorno
        else:
            os.environ.pop("ENVIRONMENT", None)
        get_settings.cache_clear()


async def _ubicacion(session: AsyncSession, pais: str, ciudad: str, nombre: str) -> uuid.UUID:
    return (
        await session.execute(
            text("""
                INSERT INTO locations (country_code, city_code, location_code, name)
                VALUES (:p,:c,:cod,:n)
                ON CONFLICT (location_code) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
            """),
            {"p": pais, "c": ciudad, "cod": f"{pais}-{ciudad}", "n": nombre},
        )
    ).scalar_one()


async def _shipment_de_prueba(
    session: AsyncSession, *, company_id: uuid.UUID, actor: uuid.UUID
) -> str:
    origen = await _ubicacion(session, "US", "MIA", "Miami")
    destino = await _ubicacion(session, "CR", "SJO", "San José")
    fila = (
        await session.execute(
            text("""
                INSERT INTO shipments
                    (company_id, created_by, current_status_code,
                     origin_location_id, destination_location_id)
                VALUES (:c, :u, 'RECEIVED', :o, :d)
                RETURNING id, shipment_number
            """),
            {"c": company_id, "u": actor, "o": origen, "d": destino},
        )
    ).one()
    await sembrar_pieza(session, fila.id)
    return str(fila.shipment_number)


class TestCapabilities:
    async def test_deshabilitado_sin_clave_configurada(
        self, cliente: AsyncClient, db_directa: AsyncSession
    ) -> None:
        await sembrar_rbac(db_directa)
        _actor, email = await _usuario_interno(db_directa)
        await db_directa.commit()
        headers = await _autenticar(cliente, email)

        with _clave_openai(None):
            resp = await cliente.get("/api/v1/copilot/capabilities", headers=headers)

        assert resp.status_code == 200
        assert resp.json()["disponible"] is False

    async def test_habilitado_con_clave_y_permiso(
        self, cliente: AsyncClient, db_directa: AsyncSession
    ) -> None:
        await sembrar_rbac(db_directa)
        _actor, email = await _usuario_interno(db_directa)
        await db_directa.commit()
        headers = await _autenticar(cliente, email)

        with _clave_openai("sk-test-no-se-usa"):
            resp = await cliente.get("/api/v1/copilot/capabilities", headers=headers)

        cuerpo = resp.json()
        assert cuerpo["disponible"] is True
        assert "consultar_estado_carga" in cuerpo["herramientas"]
        assert cuerpo["cuota_restante"] is None  # personal interno, sin tope

    async def test_deshabilitado_con_el_breaker_abierto(
        self, cliente: AsyncClient, db_directa: AsyncSession
    ) -> None:
        await sembrar_rbac(db_directa)
        _actor, email = await _usuario_interno(db_directa)
        await db_directa.commit()
        headers = await _autenticar(cliente, email)

        with _clave_openai("sk-test-no-se-usa"):
            breaker = copilot_provider._obtener_breaker()
            for _ in range(get_settings().copilot_breaker_fallos_para_abrir):
                breaker.registrar_fallo()
            resp = await cliente.get("/api/v1/copilot/capabilities", headers=headers)

        assert resp.json()["disponible"] is False

    async def test_cliente_ve_su_cuota_mensual(
        self, cliente: AsyncClient, db_directa: AsyncSession
    ) -> None:
        await sembrar_rbac(db_directa)
        _actor, email, _empresa = await _usuario_cliente(db_directa)
        await db_directa.commit()
        headers = await _autenticar(cliente, email)

        with _clave_openai("sk-test-no-se-usa"):
            resp = await cliente.get("/api/v1/copilot/capabilities", headers=headers)

        assert (
            resp.json()["cuota_restante"] == get_settings().copilot_limite_mensajes_cliente_por_mes
        )


class TestRespond:
    async def test_camino_feliz_transmite_texto_por_sse(
        self, cliente: AsyncClient, db_directa: AsyncSession, usar_proveedor
    ) -> None:
        await sembrar_rbac(db_directa)
        _actor, email = await _usuario_interno(db_directa)
        await db_directa.commit()
        headers = await _autenticar(cliente, email)

        usar_proveedor(_ProveedorFalso(guion=[_texto("Todo en orden.")]))
        with _clave_openai("sk-test-no-se-usa"):
            resp = await cliente.post(
                "/api/v1/copilot/respond",
                headers=headers,
                json={"mensajes": [{"rol": "user", "contenido": "hola"}]},
            )

        assert resp.status_code == 200
        assert "event: token" in resp.text
        assert "Todo en orden." in resp.text
        assert "event: fin" in resp.text

    async def test_sin_clave_responde_evento_de_error_no_500(
        self, cliente: AsyncClient, db_directa: AsyncSession
    ) -> None:
        await sembrar_rbac(db_directa)
        _actor, email = await _usuario_interno(db_directa)
        await db_directa.commit()
        headers = await _autenticar(cliente, email)

        with _clave_openai(None):
            resp = await cliente.post(
                "/api/v1/copilot/respond",
                headers=headers,
                json={"mensajes": [{"rol": "user", "contenido": "hola"}]},
            )

        assert resp.status_code == 200  # SSE: el error viaja como evento, no como status
        assert "COPILOT_NO_DISPONIBLE" in resp.text

    async def test_cliente_sin_cuota_recibe_429(
        self, cliente: AsyncClient, db_directa: AsyncSession, redis, usar_proveedor
    ) -> None:
        await sembrar_rbac(db_directa)
        actor, email, _empresa = await _usuario_cliente(db_directa)
        await db_directa.commit()
        headers = await _autenticar(cliente, email)

        usar_proveedor(_ProveedorFalso(guion=[_texto("ok")] * 5))
        # Agota la cuota directamente en Redis: más rápido y más claro que
        # mandar cien requests.
        await redis.set(
            f"rl:copilot:mensual:{actor}", get_settings().copilot_limite_mensajes_cliente_por_mes
        )
        with _clave_openai("sk-test-no-se-usa"):
            resp = await cliente.post(
                "/api/v1/copilot/respond",
                headers=headers,
                json={"mensajes": [{"rol": "user", "contenido": "hola"}]},
            )

        assert resp.status_code == 429


class TestPropuestas:
    async def _crear_propuesta(
        self,
        db_directa: AsyncSession,
        *,
        creador: uuid.UUID,
        company_id: uuid.UUID | None = None,
        vencida: bool = False,
        action_code: str = AccionCopilot.CREAR_PREALERTA_BORRADOR.value,
    ) -> uuid.UUID:
        expira = datetime.now(UTC) + (timedelta(hours=-1) if vencida else timedelta(hours=1))
        propuesta_id = (
            await db_directa.execute(
                text("""
                    INSERT INTO copilot_action_proposals
                        (created_by, company_id, action_code, payload, expires_at)
                    VALUES (:creador, :company_id, :action_code, '{}'::jsonb, :expira)
                    RETURNING id
                """),
                {
                    "creador": creador,
                    "company_id": company_id,
                    "action_code": action_code,
                    "expira": expira,
                },
            )
        ).scalar_one()
        await db_directa.commit()
        return propuesta_id

    async def test_propuesta_ajena_da_404(
        self, cliente: AsyncClient, db_directa: AsyncSession
    ) -> None:
        await sembrar_rbac(db_directa)
        dueño, _ = await _usuario_interno(db_directa)
        _otro, email_otro = await _usuario_interno(db_directa)
        await db_directa.commit()
        propuesta_id = await self._crear_propuesta(db_directa, creador=dueño)
        headers_otro = await _autenticar(cliente, email_otro)

        resp = await cliente.get(f"/api/v1/copilot/proposals/{propuesta_id}", headers=headers_otro)

        assert resp.status_code == 404

    async def test_confirmar_una_propuesta_vencida_da_409_y_queda_expired(
        self, cliente: AsyncClient, db_directa: AsyncSession
    ) -> None:
        await sembrar_rbac(db_directa)
        actor, email = await _usuario_interno(db_directa)
        await db_directa.commit()
        propuesta_id = await self._crear_propuesta(db_directa, creador=actor, vencida=True)
        headers = await _autenticar(cliente, email)

        resp = await cliente.post(
            f"/api/v1/copilot/proposals/{propuesta_id}/confirm",
            headers=headers,
            json={"campos": {}},
        )

        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "COPILOT_PROPUESTA_VENCIDA"

        estado = (
            await db_directa.execute(
                text("SELECT status FROM copilot_action_proposals WHERE id = :id"),
                {"id": propuesta_id},
            )
        ).scalar_one()
        assert estado == "EXPIRED"

    async def test_confirmar_sin_ejecutor_conectado_da_409_controlado(
        self, cliente: AsyncClient, db_directa: AsyncSession
    ) -> None:
        """`procesar_factura_ocr` sigue sin ejecutor de confirmación (Fase 4
        conectó `crear_prealerta_borrador`, no las dos). El mecanismo de
        bloqueo y verificación es el definitivo; la ejecución real de esta
        acción llega en una fase posterior."""
        await sembrar_rbac(db_directa)
        actor, email = await _usuario_interno(db_directa)
        await db_directa.commit()
        propuesta_id = await self._crear_propuesta(
            db_directa, creador=actor, action_code=AccionCopilot.PROCESAR_FACTURA_OCR.value
        )
        headers = await _autenticar(cliente, email)

        resp = await cliente.post(
            f"/api/v1/copilot/proposals/{propuesta_id}/confirm",
            headers=headers,
            json={"campos": {}},
        )

        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "COPILOT_ACCION_NO_CONECTADA"

    async def test_rechazar_una_propuesta_pendiente(
        self, cliente: AsyncClient, db_directa: AsyncSession
    ) -> None:
        await sembrar_rbac(db_directa)
        actor, email = await _usuario_interno(db_directa)
        await db_directa.commit()
        propuesta_id = await self._crear_propuesta(db_directa, creador=actor)
        headers = await _autenticar(cliente, email)

        resp = await cliente.post(
            f"/api/v1/copilot/proposals/{propuesta_id}/reject", headers=headers
        )

        assert resp.status_code == 200
        estado = (
            await db_directa.execute(
                text("SELECT status FROM copilot_action_proposals WHERE id = :id"),
                {"id": propuesta_id},
            )
        ).scalar_one()
        assert estado == "REJECTED"

    async def test_rechazar_una_propuesta_ya_resuelta_da_409(
        self, cliente: AsyncClient, db_directa: AsyncSession
    ) -> None:
        await sembrar_rbac(db_directa)
        actor, email = await _usuario_interno(db_directa)
        await db_directa.commit()
        propuesta_id = await self._crear_propuesta(db_directa, creador=actor)
        headers = await _autenticar(cliente, email)

        primera = await cliente.post(
            f"/api/v1/copilot/proposals/{propuesta_id}/reject", headers=headers
        )
        segunda = await cliente.post(
            f"/api/v1/copilot/proposals/{propuesta_id}/reject", headers=headers
        )

        assert primera.status_code == 200
        assert segunda.status_code == 409

    async def test_doble_confirmacion_concurrente_gana_una_sola(
        self, cliente: AsyncClient, db_directa: AsyncSession
    ) -> None:
        """Prueba real del `FOR UPDATE`: registra un ejecutor falso temporal
        para `procesar_factura_ocr` (sin ejecutor real todavía, así que no
        interfiere con el de `crear_prealerta_borrador`), y dispara dos
        confirmaciones concurrentes sobre la MISMA propuesta PENDING."""
        await sembrar_rbac(db_directa)
        actor, email = await _usuario_interno(db_directa)
        await db_directa.commit()
        propuesta_id = await self._crear_propuesta(
            db_directa, creador=actor, action_code=AccionCopilot.PROCESAR_FACTURA_OCR.value
        )
        headers = await _autenticar(cliente, email)

        ejecuciones = 0

        async def _ejecutor_falso(session, permisos, propuesta_id, campos):
            nonlocal ejecuciones
            ejecuciones += 1
            await asyncio.sleep(0.05)  # ensancha la ventana de la carrera
            return {"ok": True}

        REGISTRO_DE_CONFIRMACION[AccionCopilot.PROCESAR_FACTURA_OCR] = _ejecutor_falso
        try:
            resultados = await asyncio.gather(
                cliente.post(
                    f"/api/v1/copilot/proposals/{propuesta_id}/confirm",
                    headers={**headers, "Idempotency-Key": "carrera-1"},
                    json={"campos": {}},
                ),
                cliente.post(
                    f"/api/v1/copilot/proposals/{propuesta_id}/confirm",
                    headers={**headers, "Idempotency-Key": "carrera-2"},
                    json={"campos": {}},
                ),
            )
        finally:
            del REGISTRO_DE_CONFIRMACION[AccionCopilot.PROCESAR_FACTURA_OCR]

        codigos = sorted(r.status_code for r in resultados)
        assert codigos == [200, 409]
        assert ejecuciones == 1

    async def test_confirmar_dos_veces_con_la_misma_idempotency_key_no_duplica(
        self, cliente: AsyncClient, db_directa: AsyncSession
    ) -> None:
        await sembrar_rbac(db_directa)
        actor, email = await _usuario_interno(db_directa)
        await db_directa.commit()
        propuesta_id = await self._crear_propuesta(
            db_directa, creador=actor, action_code=AccionCopilot.PROCESAR_FACTURA_OCR.value
        )
        headers = await _autenticar(cliente, email)

        ejecuciones = 0

        async def _ejecutor_falso(session, permisos, propuesta_id, campos):
            nonlocal ejecuciones
            ejecuciones += 1
            return {"ok": True}

        REGISTRO_DE_CONFIRMACION[AccionCopilot.PROCESAR_FACTURA_OCR] = _ejecutor_falso
        try:
            primera = await cliente.post(
                f"/api/v1/copilot/proposals/{propuesta_id}/confirm",
                headers={**headers, "Idempotency-Key": "misma-clave"},
                json={"campos": {}},
            )
            segunda = await cliente.post(
                f"/api/v1/copilot/proposals/{propuesta_id}/confirm",
                headers={**headers, "Idempotency-Key": "misma-clave"},
                json={"campos": {}},
            )
        finally:
            del REGISTRO_DE_CONFIRMACION[AccionCopilot.PROCESAR_FACTURA_OCR]

        assert primera.status_code == 200
        assert segunda.status_code == 200
        assert primera.json() == segunda.json()
        assert ejecuciones == 1


class TestProveedorFalsoDeterministico:
    """Wiring real de punta a punta (config → `_fabrica_proveedor` → router →
    ejecutor → base) — lo mismo que ejercita Playwright, pero corriendo en la
    suite normal, sin navegador. Ninguna prueba de este archivo pisa la de
    `usar_proveedor`: acá deliberadamente NO se usa `app.dependency_overrides`,
    para probar que la fábrica real elige el proveedor falso por sí sola."""

    def test_fabrica_elige_el_proveedor_falso_cuando_esta_activo(self) -> None:
        with _proveedor_falso(True):
            assert _fabrica_proveedor() is ProveedorFalsoDeterministico

    def test_fabrica_elige_el_proveedor_real_por_defecto(self) -> None:
        with _proveedor_falso(False):
            fabrica = _fabrica_proveedor()
        assert fabrica is not ProveedorFalsoDeterministico

    async def test_capabilities_no_necesita_clave_real_con_el_proveedor_falso(
        self, cliente: AsyncClient, db_directa: AsyncSession
    ) -> None:
        await sembrar_rbac(db_directa)
        _actor, email = await _usuario_interno(db_directa)
        await db_directa.commit()
        headers = await _autenticar(cliente, email)

        with _clave_openai(None), _proveedor_falso(True):
            resp = await cliente.get("/api/v1/copilot/capabilities", headers=headers)

        assert resp.json()["disponible"] is True

    async def test_respond_de_punta_a_punta_busca_la_carga_y_responde(
        self, cliente: AsyncClient, db_directa: AsyncSession
    ) -> None:
        """Sin `_ProveedorFalso` de test ni `dependency_overrides`: la fábrica
        real elige `ProveedorFalsoDeterministico`, que dispara `buscar_cargas`
        (ejecutor real, contra la base real) y cierra con el estado."""
        await sembrar_rbac(db_directa)
        await sembrar_estados(db_directa)
        actor, email = await _usuario_interno(db_directa)
        empresa = (
            await db_directa.execute(
                text(
                    "INSERT INTO companies (legal_name, status) "
                    "VALUES ('Proveedor Falso S.A.', 'ACTIVE') RETURNING id"
                )
            )
        ).scalar_one()
        numero = await _shipment_de_prueba(db_directa, company_id=empresa, actor=actor)
        await db_directa.commit()
        headers = await _autenticar(cliente, email)

        with _clave_openai(None), _proveedor_falso(True):
            resp = await cliente.post(
                "/api/v1/copilot/respond",
                headers=headers,
                json={"mensajes": [{"rol": "user", "contenido": f"¿Cómo está la carga {numero}?"}]},
            )

        assert resp.status_code == 200
        assert "event: herramienta" in resp.text
        assert "buscar_cargas" in resp.text
        assert numero in resp.text
        assert "event: fin" in resp.text


class TestConfirmarEscrituraDePuntaAPunta:
    """`POST /confirm` real contra `crear_prealerta_borrador` (Fase 4): el
    preview no pasa por HTTP acá (eso ya lo prueba
    `test_copilot_escritura.py`) — el foco es que el ENDPOINT de confirmación
    ejecute el pipeline completo, incluido el camino `FAILED`."""

    async def test_confirmar_crea_la_carga_real(
        self, cliente: AsyncClient, db_directa: AsyncSession, redis
    ) -> None:
        await sembrar_rbac(db_directa)
        await sembrar_estados(db_directa)
        actor, email, empresa = await _usuario_cliente(db_directa)
        await _ubicacion(db_directa, "US", "MIA", "Miami")
        await _ubicacion(db_directa, "CR", "SJO", "San José")
        await db_directa.commit()

        permisos = await obtener_permisos_efectivos(db_directa, redis, actor)
        preview = await executors_escritura.crear_prealerta_borrador(
            db_directa,
            permisos,
            actor,
            empresa,
            {
                "descripcion": "Repuestos varios",
                "origen_location_code": "US-MIA",
                "destino_location_code": "CR-SJO",
                "factura": "INV-E2E-1",
                "peso_kg": 10.0,
                "bulto_tipo": "BOX",
                "bulto_cantidad": 1,
            },
        )
        await db_directa.commit()
        headers = await _autenticar(cliente, email)

        resp = await cliente.post(
            f"/api/v1/copilot/proposals/{preview['id']}/confirm",
            headers=headers,
            json={"campos": {}},
        )

        assert resp.status_code == 200, resp.json()
        cuerpo = resp.json()
        assert cuerpo["resultado"]["shipment_number"].startswith("SHP-")

        estado = (
            await db_directa.execute(
                text("SELECT status FROM copilot_action_proposals WHERE id = :id"),
                {"id": preview["id"]},
            )
        ).scalar_one()
        assert estado == "CONFIRMED"

    async def test_confirmar_un_borrador_incompleto_marca_failed_y_da_422(
        self, cliente: AsyncClient, db_directa: AsyncSession, redis
    ) -> None:
        await sembrar_rbac(db_directa)
        actor, email, empresa = await _usuario_cliente(db_directa)
        await db_directa.commit()

        permisos = await obtener_permisos_efectivos(db_directa, redis, actor)
        preview = await executors_escritura.crear_prealerta_borrador(
            db_directa,
            permisos,
            actor,
            empresa,
            {
                "descripcion": "Sin origen ni bultos",
                "origen_location_code": None,
                "destino_location_code": None,
                "factura": None,
                "peso_kg": None,
                "bulto_tipo": None,
                "bulto_cantidad": None,
            },
        )
        await db_directa.commit()
        headers = await _autenticar(cliente, email)

        resp = await cliente.post(
            f"/api/v1/copilot/proposals/{preview['id']}/confirm",
            headers=headers,
            json={"campos": {}},
        )

        assert resp.status_code == 422
        estado = (
            await db_directa.execute(
                text("SELECT status FROM copilot_action_proposals WHERE id = :id"),
                {"id": preview["id"]},
            )
        ).scalar_one()
        assert estado == "FAILED"
