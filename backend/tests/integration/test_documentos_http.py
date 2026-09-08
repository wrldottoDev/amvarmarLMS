"""Endpoints de documentos por HTTP (Paso 3.1).

Cubren lo que los tests de servicio no ven: permisos, códigos de estado, el
formato de error, y que la respuesta no filtre detalles internos del storage.
"""

import io
import uuid
import zipfile

import httpx
import pytest
from scripts.seed_document_types import sembrar as sembrar_documentos
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.argon2 import hash_password
from app.modules.documents import exports, service
from app.modules.documents import router as documents_router
from tests.piezas import sembrar_pieza

pytestmark = pytest.mark.integration

PASSWORD = "una passphrase de prueba suficientemente larga"


def pdf_real() -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<</Type/Catalog>>\nendobj\ntrailer\n%%EOF\n"


@pytest.fixture(autouse=True)
def exportaciones_controladas_por_el_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """El test procesa el job explícitamente, sin depender de un worker externo."""
    monkeypatch.setattr(documents_router, "_encolar_exportacion", lambda _job_id: None)


@pytest.fixture
async def entorno(db_directa: AsyncSession, storage_de_prueba: str):
    """Un cliente con una carga propia y otra empresa con la suya."""
    await sembrar_rbac(db_directa)
    await sembrar_estados(db_directa)
    await sembrar_documentos(db_directa)
    await service.sembrar_limites(db_directa)

    marca = uuid.uuid4().hex[:8]
    email = f"docs-{marca}@pruebas.amvarmar.com"

    empresa = await _empresa(db_directa, f"Docs {marca} S.A.")
    ajena = await _empresa(db_directa, f"Ajena {marca} S.A.")

    user_id = (
        await db_directa.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e, :h, 'Ana', 'Cliente', 'ACTIVE') RETURNING id
            """),
            {"e": email, "h": hash_password(PASSWORD)},
        )
    ).scalar_one()
    await db_directa.execute(
        text("""
            INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
            SELECT :u, r.id, 'ORGANIZATION', :c FROM roles r WHERE r.code = 'CLIENT_ADMIN'
        """),
        {"u": user_id, "c": empresa},
    )
    await db_directa.execute(
        text(
            "INSERT INTO company_memberships (company_id, user_id, status) VALUES (:c,:u,'ACTIVE')"
        ),
        {"c": empresa, "u": user_id},
    )

    email_ajeno = f"ajeno-{marca}@pruebas.amvarmar.com"
    user_ajeno_id = (
        await db_directa.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e, :h, 'Otro', 'Cliente', 'ACTIVE') RETURNING id
            """),
            {"e": email_ajeno, "h": hash_password(PASSWORD)},
        )
    ).scalar_one()
    await db_directa.execute(
        text("""
            INSERT INTO user_role_assignments (user_id, role_id, scope_type, company_id)
            SELECT :u, r.id, 'ORGANIZATION', :c FROM roles r WHERE r.code = 'CLIENT_USER'
        """),
        {"u": user_ajeno_id, "c": ajena},
    )
    await db_directa.execute(
        text(
            "INSERT INTO company_memberships (company_id, user_id, status) VALUES (:c,:u,'ACTIVE')"
        ),
        {"c": ajena, "u": user_ajeno_id},
    )

    # Personal interno. Renombrar y quitar documentos son acciones de staff:
    # el nombre es cómo Operaciones y el agente aduanal encuentran el papel, y
    # quitar uno reabre el requisito que bloquea el despacho.
    email_ops = f"ops-{marca}@pruebas.amvarmar.com"
    ops_id = (
        await db_directa.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e, :h, 'Ops', 'Prueba', 'ACTIVE') RETURNING id
            """),
            {"e": email_ops, "h": hash_password(PASSWORD)},
        )
    ).scalar_one()
    await db_directa.execute(
        text("""
            INSERT INTO user_role_assignments (user_id, role_id, scope_type)
            SELECT :u, r.id, 'GLOBAL' FROM roles r WHERE r.code = 'OPS_ADMIN'
        """),
        {"u": ops_id},
    )

    origen = await _ubicacion(db_directa, "US", "MIA", "Miami")
    destino = await _ubicacion(db_directa, "CR", "SJO", "San José")

    propia = await _carga(db_directa, empresa, user_id, origen, destino)
    de_otra = await _carga(db_directa, ajena, user_ajeno_id, origen, destino)

    tipo_factura = (
        await db_directa.execute(
            text("SELECT id FROM document_types WHERE code = 'COMMERCIAL_INVOICE'")
        )
    ).scalar_one()
    tipo_packing = (
        await db_directa.execute(text("SELECT id FROM document_types WHERE code = 'PACKING_LIST'"))
    ).scalar_one()
    tipo_permiso = (
        await db_directa.execute(
            text("SELECT id FROM document_types WHERE code = 'SPECIAL_PERMIT'")
        )
    ).scalar_one()
    await db_directa.commit()

    yield {
        "email": email,
        "email_ajeno": email_ajeno,
        "email_ops": email_ops,
        "ops_id": ops_id,
        "empresa": empresa,
        "carga": propia,
        "carga_ajena": de_otra,
        "user_id": user_id,
        "user_ajeno_id": user_ajeno_id,
        "tipo_factura": tipo_factura,
        "tipo_packing": tipo_packing,
        "tipo_permiso": tipo_permiso,
        "bucket": storage_de_prueba,
    }

    # Los despachos van primero: sus enlaces referencian cargas y documentos.
    await db_directa.execute(
        text("""
            DELETE FROM dispatch_documents WHERE dispatch_request_id IN
                (SELECT id FROM dispatch_requests WHERE company_id = ANY(:c))
        """),
        {"c": [empresa, ajena]},
    )
    await db_directa.execute(
        text("""
            DELETE FROM dispatch_request_shipments WHERE dispatch_request_id IN
                (SELECT id FROM dispatch_requests WHERE company_id = ANY(:c))
        """),
        {"c": [empresa, ajena]},
    )
    await db_directa.execute(
        text("DELETE FROM user_role_assignments WHERE user_id = ANY(:u)"),
        {"u": [user_id, user_ajeno_id, ops_id]},
    )
    await db_directa.execute(
        text("DELETE FROM dispatch_requests WHERE company_id = ANY(:c)"),
        {"c": [empresa, ajena]},
    )
    await db_directa.execute(
        text("DELETE FROM document_export_jobs WHERE company_id = ANY(:c)"),
        {"c": [empresa, ajena]},
    )
    await db_directa.execute(
        text("DELETE FROM shipment_requirements WHERE shipment_id = ANY(:s)"),
        {"s": [propia, de_otra]},
    )
    await db_directa.execute(
        text("DELETE FROM shipment_documents WHERE shipment_id = ANY(:s)"),
        {"s": [propia, de_otra]},
    )
    await db_directa.execute(
        text("DELETE FROM documents WHERE company_id = ANY(:c)"), {"c": [empresa, ajena]}
    )
    await db_directa.execute(
        text("DELETE FROM shipments WHERE company_id = ANY(:c)"), {"c": [empresa, ajena]}
    )
    await db_directa.execute(
        text("DELETE FROM company_memberships WHERE company_id = ANY(:c)"),
        {"c": [empresa, ajena]},
    )
    await db_directa.execute(
        text("DELETE FROM users WHERE id = ANY(:u)"),
        {"u": [user_id, user_ajeno_id, ops_id]},
    )
    await db_directa.execute(
        text("DELETE FROM companies WHERE id = ANY(:c)"), {"c": [empresa, ajena]}
    )
    await db_directa.commit()


async def _empresa(session: AsyncSession, nombre: str) -> uuid.UUID:
    return (
        await session.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"),
            {"n": nombre},
        )
    ).scalar_one()


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


async def _carga(session, empresa, user_id, origen, destino) -> uuid.UUID:
    carga = (
        await session.execute(
            text("""
                INSERT INTO shipments
                    (company_id, created_by, current_status_code,
                     origin_location_id, destination_location_id)
                VALUES (:c,:u,'IN_TRANSIT',:o,:d) RETURNING id
            """),
            {"c": empresa, "u": user_id, "o": origen, "d": destino},
        )
    ).scalar_one()
    # Toda carga activa necesita al menos una pieza.
    await sembrar_pieza(session, carga)
    return carga


async def _autenticar(cliente: httpx.AsyncClient, email: str) -> dict[str, str]:
    respuesta = await cliente.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {respuesta.json()['access_token']}"}


async def _procesar_documento(session: AsyncSession, document_id: str) -> None:
    await service.procesar_documento(session, document_id=uuid.UUID(document_id))
    await session.commit()


async def _exportar_y_descargar(
    cliente: httpx.AsyncClient,
    session: AsyncSession,
    *,
    url: str,
    cabeceras: dict[str, str],
    payload: dict[str, str] | None = None,
) -> tuple[dict, dict, bytes]:
    if payload is None:
        solicitud = await cliente.post(url, headers=cabeceras)
    else:
        solicitud = await cliente.post(url, headers=cabeceras, json=payload)
    assert solicitud.status_code == 202
    creado = solicitud.json()
    assert creado["status"] == "PENDING"
    assert creado["download_ready"] is False

    procesado = await exports.procesar(session, job_id=uuid.UUID(creado["id"]))
    assert procesado is not None
    assert procesado.status == "READY"

    estado = await cliente.get(f"/api/v1/document-export-jobs/{creado['id']}", headers=cabeceras)
    assert estado.status_code == 200
    assert estado.json()["status"] == "READY"
    assert estado.json()["download_ready"] is True

    descarga = await cliente.get(
        f"/api/v1/document-export-jobs/{creado['id']}/download", headers=cabeceras
    )
    assert descarga.status_code == 200
    assert descarga.json()["media_type"] == "application/zip"
    assert descarga.json()["filename"].endswith(".zip")

    async with httpx.AsyncClient() as directo:
        archivo = await directo.get(descarga.json()["url"])
    assert archivo.status_code == 200
    return creado, estado.json(), archivo.content


class TestPresign:
    async def test_devuelve_url_firmada_y_limite(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/presign",
            headers=cabeceras,
            json={
                "document_type_id": str(entorno["tipo_factura"]),
                "issued_by": "PROVIDER",
                "original_name": "factura.pdf",
            },
        )

        assert r.status_code == 201
        cuerpo = r.json()
        assert cuerpo["upload_url"].startswith("http")
        assert cuerpo["expires_in_seconds"] > 0
        assert cuerpo["max_bytes"] == 250 * 1024 * 1024

    async def test_no_filtra_la_storage_key(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        """Exponerla daría una pista de la estructura del bucket."""
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/presign",
            headers=cabeceras,
            json={
                "document_type_id": str(entorno["tipo_factura"]),
                "issued_by": "PROVIDER",
                "original_name": "factura.pdf",
            },
        )

        assert "storage_key" not in r.json()

    async def test_una_carga_ajena_da_404(self, cliente: httpx.AsyncClient, entorno: dict) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.post(
            f"/api/v1/shipments/{entorno['carga_ajena']}/documents/presign",
            headers=cabeceras,
            json={
                "document_type_id": str(entorno["tipo_factura"]),
                "issued_by": "PROVIDER",
                "original_name": "factura.pdf",
            },
        )

        assert r.status_code == 404
        assert r.json()["error"]["code"] == "RECURSO_NO_ENCONTRADO"

    async def test_un_formato_no_permitido_da_422(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        """La factura no acepta hoja de cálculo; solo el packing list."""
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/presign",
            headers=cabeceras,
            json={
                "document_type_id": str(entorno["tipo_factura"]),
                "issued_by": "PROVIDER",
                "original_name": "planilla.xlsx",
            },
        )

        assert r.status_code == 422
        assert r.json()["error"]["code"] == "FORMATO_NO_PERMITIDO"

    async def test_cliente_no_puede_subir_un_tipo_reservado_a_staff(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])
        respuesta = await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/presign",
            headers=cabeceras,
            json={
                "document_type_id": str(entorno["tipo_packing"]),
                "issued_by": "PROVIDER",
                "original_name": "packing.pdf",
            },
        )

        assert respuesta.status_code == 404

    async def test_staff_no_puede_subir_un_tipo_reservado_al_cliente(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["email_ops"])
        respuesta = await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/presign",
            headers=cabeceras,
            json={
                "document_type_id": str(entorno["tipo_permiso"]),
                "issued_by": "CLIENT",
                "original_name": "permiso.pdf",
            },
        )

        assert respuesta.status_code == 404

    async def test_emisor_debe_pertenecer_al_catalogo_del_tipo(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])
        respuesta = await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/presign",
            headers=cabeceras,
            json={
                "document_type_id": str(entorno["tipo_factura"]),
                "issued_by": "AMVARMAR",
                "original_name": "factura.pdf",
            },
        )

        assert respuesta.status_code == 422
        assert respuesta.json()["error"]["code"] == "DOCUMENT_ISSUER_INVALID"

    async def test_sin_token_da_401(self, cliente: httpx.AsyncClient, entorno: dict) -> None:
        r = await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/presign",
            json={
                "document_type_id": str(entorno["tipo_factura"]),
                "issued_by": "PROVIDER",
                "original_name": "factura.pdf",
            },
        )

        assert r.status_code == 401


class TestFlujoCompleto:
    async def test_subir_completar_y_descargar(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        """El recorrido de punta a punta, con MinIO real."""
        cabeceras = await _autenticar(cliente, entorno["email"])
        contenido = pdf_real()

        presign = (
            await cliente.post(
                f"/api/v1/shipments/{entorno['carga']}/documents/presign",
                headers=cabeceras,
                json={
                    "document_type_id": str(entorno["tipo_factura"]),
                    "issued_by": "PROVIDER",
                    "original_name": "factura.pdf",
                },
            )
        ).json()

        # El cliente sube DIRECTO al storage, sin pasar por la aplicación.
        async with httpx.AsyncClient() as directo:
            subida = await directo.put(presign["upload_url"], content=contenido)
        assert subida.status_code == 200

        completar = await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/complete",
            headers=cabeceras,
            json={"document_id": presign["document_id"]},
        )

        assert completar.status_code == 202
        cuerpo = completar.json()
        assert cuerpo["upload_status"] == "PROCESSING"

        # Celery ejecuta este mismo servicio fuera del request.
        await _procesar_documento(db_directa, presign["document_id"])

        descarga = await cliente.get(
            f"/api/v1/documents/{presign['document_id']}/download", headers=cabeceras
        )

        assert descarga.status_code == 200
        assert descarga.json()["filename"] == "factura.pdf"
        assert "X-Amz-Signature" in descarga.json()["url"]

        async with httpx.AsyncClient() as directo:
            archivo = await directo.get(descarga.json()["url"])
        assert archivo.content == contenido

    async def test_subir_mueve_el_requisito_a_uploaded_pero_no_lo_satisface(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        """Paso 3.4 + ADR-0003.

        Subir el archivo NO cierra el requisito: queda en `UPLOADED`, que sigue
        bloqueando el despacho hasta que Operaciones lo verifique. Si subir
        bastara, un cliente podría desbloquear su propia carga mandando
        cualquier PDF.
        """
        cabeceras = await _autenticar(cliente, entorno["email"])

        requisito = (
            await db_directa.execute(
                text("""
                    INSERT INTO shipment_requirements
                        (shipment_id, requirement_type, document_type_id, title,
                         required_from, status, blocks_dispatch, created_by)
                    VALUES (:s, 'DOCUMENT', :t, 'Factura comercial',
                            'CLIENT', 'PENDING', true, :u)
                    RETURNING id
                """),
                {
                    "s": entorno["carga"],
                    "t": entorno["tipo_factura"],
                    "u": entorno["user_id"],
                },
            )
        ).scalar_one()
        await db_directa.commit()

        presign = (
            await cliente.post(
                f"/api/v1/shipments/{entorno['carga']}/documents/presign",
                headers=cabeceras,
                json={
                    "document_type_id": str(entorno["tipo_factura"]),
                    "issued_by": "PROVIDER",
                    "original_name": "factura.pdf",
                },
            )
        ).json()

        async with httpx.AsyncClient() as directo:
            await directo.put(presign["upload_url"], content=pdf_real())

        # Antes de completar, el requisito sigue intacto: reservar la subida no
        # es haber subido nada.
        assert await _estado_requisito(db_directa, requisito) == "PENDING"

        completar = await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/complete",
            headers=cabeceras,
            json={"document_id": presign["document_id"]},
        )
        assert completar.status_code == 202

        # El requisito cambia cuando el worker confirma todos los bytes.
        assert await _estado_requisito(db_directa, requisito) == "PENDING"
        await _procesar_documento(db_directa, presign["document_id"])

        assert await _estado_requisito(db_directa, requisito) == "UPLOADED"

    async def test_completar_con_contenido_que_no_coincide(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        """Pasó la validación de extensión al preparar, subió otra cosa."""
        cabeceras = await _autenticar(cliente, entorno["email"])

        presign = (
            await cliente.post(
                f"/api/v1/shipments/{entorno['carga']}/documents/presign",
                headers=cabeceras,
                json={
                    "document_type_id": str(entorno["tipo_factura"]),
                    "issued_by": "PROVIDER",
                    "original_name": "factura.pdf",
                },
            )
        ).json()

        async with httpx.AsyncClient() as directo:
            # PNG con nombre .pdf
            await directo.put(presign["upload_url"], content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)

        r = await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/complete",
            headers=cabeceras,
            json={"document_id": presign["document_id"]},
        )

        assert r.status_code == 422
        assert r.json()["error"]["code"] in {
            "CONTENIDO_NO_COINCIDE_CON_LA_EXTENSION",
            "FORMATO_NO_PERMITIDO",
        }

    async def test_el_rechazo_queda_auditado(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        """Una subida rechazada suele ser un error del cliente; una racha de
        ellas es otra cosa."""
        cabeceras = await _autenticar(cliente, entorno["email"])
        presign = (
            await cliente.post(
                f"/api/v1/shipments/{entorno['carga']}/documents/presign",
                headers=cabeceras,
                json={
                    "document_type_id": str(entorno["tipo_factura"]),
                    "issued_by": "PROVIDER",
                    "original_name": "factura.pdf",
                },
            )
        ).json()

        # Nunca sube nada y llama a complete.
        await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/complete",
            headers=cabeceras,
            json={"document_id": presign["document_id"]},
        )

        total = (
            await db_directa.execute(
                text("""
                    SELECT count(*) FROM audit_logs
                    WHERE action = 'document.upload.rejected' AND resource_id = :id
                """),
                {"id": uuid.UUID(presign["document_id"])},
            )
        ).scalar_one()
        assert total == 1


class TestDescarga:
    async def test_un_documento_inexistente_da_404(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.get(f"/api/v1/documents/{uuid.uuid4()}/download", headers=cabeceras)

        assert r.status_code == 404

    async def test_sin_token_da_401(self, cliente: httpx.AsyncClient) -> None:
        r = await cliente.get(f"/api/v1/documents/{uuid.uuid4()}/download")

        assert r.status_code == 401


async def _estado_requisito(session: AsyncSession, requirement_id: uuid.UUID) -> str:
    estado: str = (
        await session.execute(
            text("SELECT status FROM shipment_requirements WHERE id = :id"),
            {"id": requirement_id},
        )
    ).scalar_one()
    return estado


class TestExpediente:
    """Qué documentos pide la carga y cuáles ya tiene.

    Con solo un contador, la interfaz puede decir "faltan documentos" pero no
    cuál, y esa es justo la información que hace falta para resolverlo.
    """

    async def test_lista_requisitos_documentos_y_tipos(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])
        await db_directa.execute(
            text("""
                INSERT INTO shipment_requirements
                    (shipment_id, requirement_type, document_type_id, title,
                     required_from, status, blocks_dispatch, created_by)
                VALUES (:s, 'DOCUMENT', :t, 'Factura comercial',
                        'CLIENT', 'PENDING', true, :u)
            """),
            {"s": entorno["carga"], "t": entorno["tipo_factura"], "u": entorno["user_id"]},
        )
        await db_directa.commit()

        r = await cliente.get(f"/api/v1/shipments/{entorno['carga']}/documents", headers=cabeceras)

        assert r.status_code == 200
        cuerpo = r.json()
        assert len(cuerpo["requisitos"]) == 1
        requisito = cuerpo["requisitos"][0]
        assert requisito["label"] == "Factura comercial"
        assert requisito["status"] == "PENDING"
        assert requisito["blocks_dispatch"] is True
        # Los formatos vienen del tipo: sin eso la interfaz no puede decir qué
        # archivo aceptar y el rechazo llegaría después de subir.
        assert requisito["allowed_formats"]
        # Todavía no hay documento subido para ese requisito.
        assert requisito["document_id"] is None
        assert cuerpo["tipos"]

    async def test_una_reserva_sin_archivo_no_cuenta_como_subido(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        """`presign` crea la fila, pero el archivo puede no llegar nunca.

        Si el expediente devolviera ese documento, la interfaz ofrecería ver
        algo que no existe.
        """
        cabeceras = await _autenticar(cliente, entorno["email"])
        await _abrir_requisito(db_directa, entorno)

        await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/presign",
            headers=cabeceras,
            json={
                "document_type_id": str(entorno["tipo_factura"]),
                "issued_by": "PROVIDER",
                "original_name": "factura.pdf",
            },
        )

        cuerpo = (
            await cliente.get(f"/api/v1/shipments/{entorno['carga']}/documents", headers=cabeceras)
        ).json()

        assert cuerpo["requisitos"][0]["document_id"] is None

    async def test_el_requisito_apunta_al_documento_ya_subido(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        """Así se puede abrir desde el mismo renglón que lo pide."""
        cabeceras = await _autenticar(cliente, entorno["email"])
        await _abrir_requisito(db_directa, entorno)

        presign = (
            await cliente.post(
                f"/api/v1/shipments/{entorno['carga']}/documents/presign",
                headers=cabeceras,
                json={
                    "document_type_id": str(entorno["tipo_factura"]),
                    "issued_by": "PROVIDER",
                    "original_name": "factura.pdf",
                },
            )
        ).json()

        async with httpx.AsyncClient() as directo:
            await directo.put(presign["upload_url"], content=pdf_real())

        await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/complete",
            headers=cabeceras,
            json={"document_id": presign["document_id"]},
        )
        await _procesar_documento(db_directa, presign["document_id"])

        cuerpo = (
            await cliente.get(f"/api/v1/shipments/{entorno['carga']}/documents", headers=cabeceras)
        ).json()

        assert cuerpo["requisitos"][0]["document_id"] == presign["document_id"]
        # Subir NO satisface el requisito (ADR-0003): queda en revisión.
        assert cuerpo["requisitos"][0]["status"] == "UPLOADED"

    async def test_no_devuelve_el_expediente_de_otra_empresa(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.get(
            f"/api/v1/shipments/{entorno['carga_ajena']}/documents", headers=cabeceras
        )

        assert r.status_code == 404

    async def test_sin_token_da_401(self, cliente: httpx.AsyncClient, entorno: dict) -> None:
        r = await cliente.get(f"/api/v1/shipments/{entorno['carga']}/documents")

        assert r.status_code == 401


async def _abrir_requisito(session: AsyncSession, entorno: dict) -> None:
    await session.execute(
        text("""
            INSERT INTO shipment_requirements
                (shipment_id, requirement_type, document_type_id, title,
                 required_from, status, blocks_dispatch, created_by)
            VALUES (:s, 'DOCUMENT', :t, 'Factura comercial',
                    'CLIENT', 'PENDING', true, :u)
        """),
        {"s": entorno["carga"], "t": entorno["tipo_factura"], "u": entorno["user_id"]},
    )
    await session.commit()


class TestGestionDeArchivos:
    """Las acciones `rename` y `delete` de `edit_files` del sistema viejo.

    Las dos son de personal interno, igual que en el original: `edit_files`
    estaba bajo `@staff_member_required`.
    """

    async def _subido(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession, nombre: str
    ) -> str:
        cabeceras = await _autenticar(cliente, entorno["email"])
        presign = (
            await cliente.post(
                f"/api/v1/shipments/{entorno['carga']}/documents/presign",
                headers=cabeceras,
                json={
                    "document_type_id": str(entorno["tipo_factura"]),
                    "issued_by": "PROVIDER",
                    "original_name": nombre,
                },
            )
        ).json()
        async with httpx.AsyncClient() as directo:
            await directo.put(presign["upload_url"], content=pdf_real())
        await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/complete",
            headers=cabeceras,
            json={"document_id": presign["document_id"]},
        )
        await _procesar_documento(db_directa, presign["document_id"])
        documento_id: str = presign["document_id"]
        return documento_id

    async def test_renombrar_cambia_el_nombre_visible(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        documento = await self._subido(cliente, entorno, db_directa, "malnombre.pdf")
        cabeceras = await _autenticar(cliente, entorno["email_ops"])

        r = await cliente.patch(
            f"/api/v1/documents/{documento}",
            headers=cabeceras,
            json={"original_name": "Factura ATC 2026.pdf"},
        )

        assert r.status_code == 200
        assert r.json()["original_name"] == "Factura ATC 2026.pdf"

    async def test_un_nombre_con_ruta_se_sanea(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        """Un nombre escrito a mano trae barras tan fácil como uno del navegador.

        El nombre visible se guarda tal cual —es lo que la persona quiso poner—
        pero `safe_name`, que es el que se usa al armar el ZIP y la descarga,
        pasa por el mismo saneo que al subir.
        """
        documento = await self._subido(cliente, entorno, db_directa, "ok.pdf")
        cabeceras = await _autenticar(cliente, entorno["email_ops"])

        await cliente.patch(
            f"/api/v1/documents/{documento}",
            headers=cabeceras,
            json={"original_name": "../../etc/passwd"},
        )

        safe = (
            await db_directa.execute(
                text("SELECT safe_name FROM documents WHERE id = :d"),
                {"d": uuid.UUID(documento)},
            )
        ).scalar_one()

        assert "/" not in safe
        assert ".." not in safe

    async def test_quitar_lo_saca_del_expediente_sin_borrar_el_archivo(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        """El objeto sigue en el storage: puede ser evidencia de un incidente."""
        documento = await self._subido(cliente, entorno, db_directa, "quitar.pdf")
        cabeceras = await _autenticar(cliente, entorno["email_ops"])

        r = await cliente.delete(
            f"/api/v1/documents/{documento}",
            headers=cabeceras,
            params={"motivo": "Documento cargado por error."},
        )
        assert r.status_code == 204

        fila = (
            await db_directa.execute(
                text("SELECT deleted_at, storage_key FROM documents WHERE id = :d"),
                {"d": uuid.UUID(documento)},
            )
        ).one()
        assert fila.deleted_at is not None
        assert fila.storage_key

        expediente = await cliente.get(
            f"/api/v1/shipments/{entorno['carga']}/documents", headers=cabeceras
        )
        nombres = [d["original_name"] for d in expediente.json()["documentos"]]
        assert "quitar.pdf" not in nombres

    async def test_quitarlo_reabre_su_requisito(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        """Si no, la carga pasaría a despacho sin el papel que la habilita."""
        documento = await self._subido(cliente, entorno, db_directa, "factura.pdf")
        cabeceras = await _autenticar(cliente, entorno["email_ops"])

        await cliente.delete(
            f"/api/v1/documents/{documento}",
            headers=cabeceras,
            params={"motivo": "La factura no corresponde a la carga."},
        )

        estado = (
            await db_directa.execute(
                text("""
                    SELECT status FROM shipment_requirements
                    WHERE shipment_id = :s AND document_type_id = :t
                      AND requirement_type = 'DOCUMENT'
                """),
                {"s": entorno["carga"], "t": entorno["tipo_factura"]},
            )
        ).scalar_one_or_none()

        assert estado in (None, "PENDING")

    async def test_un_documento_ya_quitado_da_404(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        documento = await self._subido(cliente, entorno, db_directa, "dos-veces.pdf")
        cabeceras = await _autenticar(cliente, entorno["email_ops"])

        assert (
            await cliente.delete(
                f"/api/v1/documents/{documento}",
                headers=cabeceras,
                params={"motivo": "Primera invalidación."},
            )
        ).status_code == 204
        assert (
            await cliente.delete(
                f"/api/v1/documents/{documento}",
                headers=cabeceras,
                params={"motivo": "Segunda invalidación."},
            )
        ).status_code == 404


class TestDescargaMasiva:
    """El "descargar todos" del sistema viejo.

    ADR-0009 prohibió los ZIP en la SUBIDA, no en la descarga: uno que entra
    puede esconder cualquier cosa; uno que sale lo armamos nosotros.
    """

    async def _documento_listo(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession, nombre: str
    ) -> str:
        cabeceras = await _autenticar(cliente, entorno["email"])
        presign = (
            await cliente.post(
                f"/api/v1/shipments/{entorno['carga']}/documents/presign",
                headers=cabeceras,
                json={
                    "document_type_id": str(entorno["tipo_factura"]),
                    "issued_by": "PROVIDER",
                    "original_name": nombre,
                },
            )
        ).json()

        async with httpx.AsyncClient() as directo:
            await directo.put(presign["upload_url"], content=pdf_real())

        await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/complete",
            headers=cabeceras,
            json={"document_id": presign["document_id"]},
        )
        # El antivirus corre aparte; acá se simula su veredicto.
        await db_directa.execute(
            text("""
                UPDATE documents SET upload_status = 'READY'
                WHERE id = :id
            """),
            {"id": uuid.UUID(presign["document_id"])},
        )
        await db_directa.commit()
        return presign["document_id"]

    async def test_devuelve_un_zip_con_los_documentos(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        await self._documento_listo(cliente, entorno, db_directa, "factura.pdf")
        await self._documento_listo(cliente, entorno, db_directa, "packing.pdf")
        cabeceras = await _autenticar(cliente, entorno["email"])

        creado, estado, contenido = await _exportar_y_descargar(
            cliente,
            db_directa,
            url=f"/api/v1/shipments/{entorno['carga']}/documents/exports",
            cabeceras=cabeceras,
        )

        assert estado["size_bytes"] == len(contenido)
        assert len(estado["sha256"]) == 64

        with zipfile.ZipFile(io.BytesIO(contenido)) as paquete:
            nombres = paquete.namelist()
            assert len(nombres) == 2
            # Van agrupados por tipo de documento, para que el agente aduanal no
            # tenga que adivinar qué es cada archivo.
            assert all(n.startswith("COMMERCIAL_INVOICE/") for n in nombres)

        # La misma fuente no crea ni vuelve a procesar otro ZIP.
        repetido = await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/exports",
            headers=cabeceras,
        )
        assert repetido.status_code == 202
        assert repetido.json()["id"] == creado["id"]
        assert repetido.json()["status"] == "READY"

    async def test_no_incluye_lo_que_quedo_a_medio_subir(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        """Un archivo truncado dentro del ZIP es peor que su ausencia.

        Y la ausencia se anota dentro del propio ZIP: omitirlo en silencio haría
        creer que la carga no tenía ese documento.
        """
        limpio = await self._documento_listo(cliente, entorno, db_directa, "bueno.pdf")
        incompleto = await self._documento_listo(cliente, entorno, db_directa, "malo.pdf")
        await db_directa.execute(
            text("UPDATE documents SET upload_status = 'UPLOADING' WHERE id = :id"),
            {"id": uuid.UUID(incompleto)},
        )
        await db_directa.commit()
        cabeceras = await _autenticar(cliente, entorno["email"])

        _, _, contenido = await _exportar_y_descargar(
            cliente,
            db_directa,
            url=f"/api/v1/shipments/{entorno['carga']}/documents/exports",
            cabeceras=cabeceras,
        )

        with zipfile.ZipFile(io.BytesIO(contenido)) as paquete:
            nombres = paquete.namelist()
            assert not any("malo.pdf" in n for n in nombres)
            assert any("bueno.pdf" in n for n in nombres)

            aviso = paquete.read("DOCUMENTOS-NO-INCLUIDOS.txt").decode()
            assert "malo.pdf" in aviso
            assert "subida no se completó" in aviso
        assert limpio

    async def test_dos_archivos_con_el_mismo_nombre_no_se_pisan(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        """Dentro de un ZIP, el mismo nombre dos veces hace que uno desaparezca
        al extraer."""
        await self._documento_listo(cliente, entorno, db_directa, "factura.pdf")
        await self._documento_listo(cliente, entorno, db_directa, "factura.pdf")
        cabeceras = await _autenticar(cliente, entorno["email"])

        _, _, contenido = await _exportar_y_descargar(
            cliente,
            db_directa,
            url=f"/api/v1/shipments/{entorno['carga']}/documents/exports",
            cabeceras=cabeceras,
        )

        with zipfile.ZipFile(io.BytesIO(contenido)) as paquete:
            nombres = paquete.namelist()
            assert len(nombres) == 2
            assert len(set(nombres)) == 2

    async def test_no_devuelve_los_de_otra_empresa(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.post(
            f"/api/v1/shipments/{entorno['carga_ajena']}/documents/exports",
            headers=cabeceras,
        )

        assert r.status_code == 404

    async def test_otra_empresa_no_puede_consultar_ni_descargar_el_job(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        await self._documento_listo(cliente, entorno, db_directa, "privado.pdf")
        cabeceras = await _autenticar(cliente, entorno["email"])
        solicitud = await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/exports",
            headers=cabeceras,
        )
        assert solicitud.status_code == 202

        cabeceras_ajenas = await _autenticar(cliente, entorno["email_ajeno"])
        job_id = solicitud.json()["id"]
        estado = await cliente.get(
            f"/api/v1/document-export-jobs/{job_id}", headers=cabeceras_ajenas
        )
        descarga = await cliente.get(
            f"/api/v1/document-export-jobs/{job_id}/download",
            headers=cabeceras_ajenas,
        )

        assert estado.status_code == 404
        assert descarga.status_code == 404

    async def test_una_carga_sin_documentos_lo_dice(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/exports", headers=cabeceras
        )

        assert r.status_code == 404


class TestDocumentosDeDespacho:
    """El BL y las facturas cuelgan de la solicitud, no de una carga suelta.

    En el sistema viejo eran dos pantallas distintas porque eran dos tablas
    distintas. Para quien las mira son los papeles del despacho.
    """

    async def _despacho(self, db_directa: AsyncSession, entorno: dict) -> uuid.UUID:
        despacho = (
            await db_directa.execute(
                text("""
                    INSERT INTO dispatch_requests
                        (dispatch_number, company_id, requested_by, method, status, requested_at)
                    VALUES (siguiente_dispatch_number(), :c, :u, 'SEA', 'APPROVED', now())
                    RETURNING id
                """),
                {"c": entorno["empresa"], "u": entorno["user_id"]},
            )
        ).scalar_one()
        await db_directa.execute(
            text("""
                INSERT INTO dispatch_request_shipments (dispatch_request_id, shipment_id)
                VALUES (:d, :s)
            """),
            {"d": despacho, "s": entorno["carga"]},
        )
        await db_directa.commit()
        return despacho

    async def test_adjuntar_y_listar(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        despacho = await self._despacho(db_directa, entorno)
        cabeceras = await _autenticar(cliente, entorno["email_ops"])
        tipo_bl = (
            await db_directa.execute(text("SELECT id FROM document_types WHERE code = 'BL'"))
        ).scalar_one()

        presign = await cliente.post(
            f"/api/v1/dispatch-requests/{despacho}/documents/presign",
            headers=cabeceras,
            json={
                "document_type_id": str(tipo_bl),
                "issued_by": "CARRIER",
                "original_name": "bl.pdf",
            },
        )
        assert presign.status_code == 201

        listado = await cliente.get(
            f"/api/v1/dispatch-requests/{despacho}/documents", headers=cabeceras
        )

        assert listado.status_code == 200
        documentos = listado.json()
        assert len(documentos) == 1
        assert documentos[0]["document_type_code"] == "BL"
        fuga = (
            await db_directa.execute(
                text("SELECT count(*) FROM shipment_documents WHERE document_id = :id"),
                {"id": uuid.UUID(presign.json()["document_id"])},
            )
        ).scalar_one()
        assert fuga == 0

    async def test_cliente_no_puede_adjuntar_el_bl(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        despacho = await self._despacho(db_directa, entorno)
        cabeceras = await _autenticar(cliente, entorno["email"])
        tipo_bl = (
            await db_directa.execute(text("SELECT id FROM document_types WHERE code = 'BL'"))
        ).scalar_one()

        respuesta = await cliente.post(
            f"/api/v1/dispatch-requests/{despacho}/documents/presign",
            headers=cabeceras,
            json={
                "document_type_id": str(tipo_bl),
                "issued_by": "CARRIER",
                "original_name": "bl.pdf",
            },
        )

        assert respuesta.status_code == 404

    async def test_descargar_los_bls_juntos(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        """Un despacho puede llevar varios y el cliente los necesita juntos."""
        despacho = await self._despacho(db_directa, entorno)
        cabeceras = await _autenticar(cliente, entorno["email_ops"])
        tipo_bl = (
            await db_directa.execute(text("SELECT id FROM document_types WHERE code = 'BL'"))
        ).scalar_one()

        for nombre in ("bl-uno.pdf", "bl-dos.pdf"):
            presign = (
                await cliente.post(
                    f"/api/v1/dispatch-requests/{despacho}/documents/presign",
                    headers=cabeceras,
                    json={
                        "document_type_id": str(tipo_bl),
                        "issued_by": "CARRIER",
                        "original_name": nombre,
                    },
                )
            ).json()
            async with httpx.AsyncClient() as directo:
                await directo.put(presign["upload_url"], content=pdf_real())
            completar = await cliente.post(
                f"/api/v1/dispatch-requests/{despacho}/documents/complete",
                headers=cabeceras,
                json={"document_id": presign["document_id"]},
            )
            assert completar.status_code == 202
            await _procesar_documento(db_directa, presign["document_id"])

        _, _, contenido = await _exportar_y_descargar(
            cliente,
            db_directa,
            url=f"/api/v1/dispatch-requests/{despacho}/documents/exports",
            cabeceras=cabeceras,
            payload={"kind": "BLS"},
        )

        with zipfile.ZipFile(io.BytesIO(contenido)) as paquete:
            assert len(paquete.namelist()) == 2

    async def test_sin_bls_lo_dice(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        despacho = await self._despacho(db_directa, entorno)
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.post(
            f"/api/v1/dispatch-requests/{despacho}/documents/exports",
            headers=cabeceras,
            json={"kind": "BLS"},
        )

        assert r.status_code == 404

    async def test_no_devuelve_los_de_otra_empresa(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        ajeno = (
            await db_directa.execute(
                text("""
                    INSERT INTO dispatch_requests
                        (dispatch_number, company_id, requested_by, method, status, requested_at)
                    SELECT siguiente_dispatch_number(), c.id, :u, 'SEA', 'PENDING', now()
                    FROM companies c WHERE c.id <> :propia LIMIT 1
                    RETURNING id
                """),
                {"u": entorno["user_id"], "propia": entorno["empresa"]},
            )
        ).scalar_one()
        await db_directa.commit()
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.get(f"/api/v1/dispatch-requests/{ajeno}/documents", headers=cabeceras)

        assert r.status_code == 404
