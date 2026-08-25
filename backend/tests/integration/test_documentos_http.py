"""Endpoints de documentos por HTTP (Paso 3.1).

Cubren lo que los tests de servicio no ven: permisos, códigos de estado, el
formato de error, y que la respuesta no filtre detalles internos del storage.
"""

import uuid

import httpx
import pytest
from scripts.seed_document_types import sembrar as sembrar_documentos
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.argon2 import hash_password
from app.modules.documents import service

pytestmark = pytest.mark.integration

PASSWORD = "una passphrase de prueba suficientemente larga"


def pdf_real() -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<</Type/Catalog>>\nendobj\ntrailer\n%%EOF\n"


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

    origen = await _ubicacion(db_directa, "US", "MIA", "Miami")
    destino = await _ubicacion(db_directa, "CR", "SJO", "San José")

    propia = await _carga(db_directa, empresa, user_id, origen, destino)
    de_otra = await _carga(db_directa, ajena, user_id, origen, destino)

    tipo_factura = (
        await db_directa.execute(
            text("SELECT id FROM document_types WHERE code = 'COMMERCIAL_INVOICE'")
        )
    ).scalar_one()
    tipo_packing = (
        await db_directa.execute(text("SELECT id FROM document_types WHERE code = 'PACKING_LIST'"))
    ).scalar_one()
    await db_directa.commit()

    yield {
        "email": email,
        "empresa": empresa,
        "carga": propia,
        "carga_ajena": de_otra,
        "user_id": user_id,
        "tipo_factura": tipo_factura,
        "tipo_packing": tipo_packing,
        "bucket": storage_de_prueba,
    }

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
    await db_directa.execute(text("DELETE FROM users WHERE email = :e"), {"e": email})
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
    return (
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


async def _autenticar(cliente: httpx.AsyncClient, email: str) -> dict[str, str]:
    respuesta = await cliente.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {respuesta.json()['access_token']}"}


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
                "original_name": "planilla.xlsx",
            },
        )

        assert r.status_code == 422
        assert r.json()["error"]["code"] == "FORMATO_NO_PERMITIDO"

    async def test_sin_token_da_401(self, cliente: httpx.AsyncClient, entorno: dict) -> None:
        r = await cliente.post(
            f"/api/v1/shipments/{entorno['carga']}/documents/presign",
            json={
                "document_type_id": str(entorno["tipo_factura"]),
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

        assert completar.status_code == 200
        cuerpo = completar.json()
        assert cuerpo["media_type"] == "application/pdf"
        assert cuerpo["size_bytes"] == len(contenido)
        # Todavía no descargable: falta el antivirus (Paso 3.2).
        assert cuerpo["upload_status"] == "PROCESSING"
        assert cuerpo["scan_status"] == "PENDING"

        sin_escanear = await cliente.get(
            f"/api/v1/documents/{presign['document_id']}/download", headers=cabeceras
        )
        assert sin_escanear.status_code == 409
        assert sin_escanear.json()["error"]["code"] == "DOCUMENTO_NO_DISPONIBLE"

        # Simular que el antivirus lo aprobó.
        await db_directa.execute(
            text("""
                UPDATE documents SET scan_status = 'CLEAN', upload_status = 'READY'
                WHERE id = :id
            """),
            {"id": uuid.UUID(presign["document_id"])},
        )
        await db_directa.commit()

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
        assert completar.status_code == 200

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
