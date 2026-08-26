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
        text("DELETE FROM dispatch_requests WHERE company_id = ANY(:c)"),
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


class TestDescargaMasiva:
    """El "descargar todos" del sistema viejo.

    ADR-0009 prohibió los ZIP en la SUBIDA, no en la descarga: uno que entra
    puede esconder cualquier cosa y el antivirus no lo abre; uno que sale lo
    armamos nosotros con archivos que ya escaneamos.
    """

    async def _documento_listo(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession, nombre: str
    ) -> str:
        cabeceras = await _autenticar(cliente, entorno["email"])
        presign = (
            await cliente.post(
                f"/api/v1/shipments/{entorno['carga']}/documents/presign",
                headers=cabeceras,
                json={"document_type_id": str(entorno["tipo_factura"]), "original_name": nombre},
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
                UPDATE documents SET scan_status = 'CLEAN', upload_status = 'READY'
                WHERE id = :id
            """),
            {"id": uuid.UUID(presign["document_id"])},
        )
        await db_directa.commit()
        return presign["document_id"]

    async def test_devuelve_un_zip_con_los_documentos(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        import io
        import zipfile

        await self._documento_listo(cliente, entorno, db_directa, "factura.pdf")
        await self._documento_listo(cliente, entorno, db_directa, "packing.pdf")
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.get(
            f"/api/v1/shipments/{entorno['carga']}/documents/download-all", headers=cabeceras
        )

        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert ".zip" in r.headers["content-disposition"]

        with zipfile.ZipFile(io.BytesIO(r.content)) as paquete:
            nombres = paquete.namelist()
            assert len(nombres) == 2
            # Van agrupados por tipo de documento, para que el agente aduanal no
            # tenga que adivinar qué es cada archivo.
            assert all(n.startswith("COMMERCIAL_INVOICE/") for n in nombres)

    async def test_no_incluye_lo_que_no_esta_escaneado(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        """Es exactamente lo que el escaneo existe para impedir.

        Y la ausencia se anota dentro del propio ZIP: omitirlo en silencio haría
        creer que la carga no tenía ese documento.
        """
        import io
        import zipfile

        limpio = await self._documento_listo(cliente, entorno, db_directa, "bueno.pdf")
        infectado = await self._documento_listo(cliente, entorno, db_directa, "malo.pdf")
        await db_directa.execute(
            text("UPDATE documents SET scan_status = 'INFECTED' WHERE id = :id"),
            {"id": uuid.UUID(infectado)},
        )
        await db_directa.commit()
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.get(
            f"/api/v1/shipments/{entorno['carga']}/documents/download-all", headers=cabeceras
        )

        with zipfile.ZipFile(io.BytesIO(r.content)) as paquete:
            nombres = paquete.namelist()
            assert not any("malo.pdf" in n for n in nombres)
            assert any("bueno.pdf" in n for n in nombres)

            aviso = paquete.read("DOCUMENTOS-NO-INCLUIDOS.txt").decode()
            assert "malo.pdf" in aviso
            assert "amenaza" in aviso
        assert limpio

    async def test_dos_archivos_con_el_mismo_nombre_no_se_pisan(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        """Dentro de un ZIP, el mismo nombre dos veces hace que uno desaparezca
        al extraer."""
        import io
        import zipfile

        await self._documento_listo(cliente, entorno, db_directa, "factura.pdf")
        await self._documento_listo(cliente, entorno, db_directa, "factura.pdf")
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.get(
            f"/api/v1/shipments/{entorno['carga']}/documents/download-all", headers=cabeceras
        )

        with zipfile.ZipFile(io.BytesIO(r.content)) as paquete:
            nombres = paquete.namelist()
            assert len(nombres) == 2
            assert len(set(nombres)) == 2

    async def test_no_devuelve_los_de_otra_empresa(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.get(
            f"/api/v1/shipments/{entorno['carga_ajena']}/documents/download-all",
            headers=cabeceras,
        )

        assert r.status_code == 404

    async def test_una_carga_sin_documentos_lo_dice(
        self, cliente: httpx.AsyncClient, entorno: dict
    ) -> None:
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.get(
            f"/api/v1/shipments/{entorno['carga']}/documents/download-all", headers=cabeceras
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
        cabeceras = await _autenticar(cliente, entorno["email"])
        tipo_bl = (
            await db_directa.execute(text("SELECT id FROM document_types WHERE code = 'BL'"))
        ).scalar_one()

        presign = await cliente.post(
            f"/api/v1/dispatch-requests/{despacho}/documents/presign",
            headers=cabeceras,
            json={"document_type_id": str(tipo_bl), "original_name": "bl.pdf"},
        )
        assert presign.status_code == 201

        listado = await cliente.get(
            f"/api/v1/dispatch-requests/{despacho}/documents", headers=cabeceras
        )

        assert listado.status_code == 200
        documentos = listado.json()
        assert len(documentos) == 1
        assert documentos[0]["document_type_code"] == "BL"

    async def test_descargar_los_bls_juntos(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        """Un despacho puede llevar varios y el cliente los necesita juntos."""
        import io
        import zipfile

        despacho = await self._despacho(db_directa, entorno)
        cabeceras = await _autenticar(cliente, entorno["email"])
        tipo_bl = (
            await db_directa.execute(text("SELECT id FROM document_types WHERE code = 'BL'"))
        ).scalar_one()

        for nombre in ("bl-uno.pdf", "bl-dos.pdf"):
            presign = (
                await cliente.post(
                    f"/api/v1/dispatch-requests/{despacho}/documents/presign",
                    headers=cabeceras,
                    json={"document_type_id": str(tipo_bl), "original_name": nombre},
                )
            ).json()
            async with httpx.AsyncClient() as directo:
                await directo.put(presign["upload_url"], content=pdf_real())
            await cliente.post(
                f"/api/v1/shipments/{entorno['carga']}/documents/complete",
                headers=cabeceras,
                json={"document_id": presign["document_id"]},
            )
            await db_directa.execute(
                text("""
                    UPDATE documents SET scan_status='CLEAN', upload_status='READY'
                    WHERE id = :id
                """),
                {"id": uuid.UUID(presign["document_id"])},
            )
        await db_directa.commit()

        r = await cliente.get(
            f"/api/v1/dispatch-requests/{despacho}/documents/bls", headers=cabeceras
        )

        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.content)) as paquete:
            assert len(paquete.namelist()) == 2

    async def test_sin_bls_lo_dice(
        self, cliente: httpx.AsyncClient, entorno: dict, db_directa: AsyncSession
    ) -> None:
        despacho = await self._despacho(db_directa, entorno)
        cabeceras = await _autenticar(cliente, entorno["email"])

        r = await cliente.get(
            f"/api/v1/dispatch-requests/{despacho}/documents/bls", headers=cabeceras
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
