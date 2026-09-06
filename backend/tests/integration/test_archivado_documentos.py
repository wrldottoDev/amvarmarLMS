"""Recompresión de documentos de cargas archivadas (ADR-0007).

Ghostscript (PDF sin firma) se salta si no está instalado — no es una
dependencia Python, y no todo entorno lo tiene. Pillow (imagen) y gzip
(contenedor de PDF firmado) sí corren siempre: son dependencia dura o stdlib.
"""

import shutil
import uuid
from io import BytesIO
from typing import Any

import pytest
from PIL import Image
from scripts.seed_document_types import sembrar as sembrar_documentos
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.storage import s3
from app.modules.documents import archivado, service
from app.modules.rbac.catalog import Perm
from app.modules.rbac.models import ScopeType
from app.modules.rbac.service import PermisoEfectivo, PermisosEfectivos
from tests.piezas import sembrar_pieza

pytestmark = pytest.mark.integration

_SIN_GHOSTSCRIPT = shutil.which("gs") is None


def _jpeg_real() -> bytes:
    """Imagen real que Pillow puede abrir y volver a guardar — no solo
    magic bytes válidos, un archivo genuinamente decodificable."""
    buffer = BytesIO()
    Image.new("RGB", (64, 64), color=(200, 50, 50)).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _pdf_real() -> bytes:
    """PDF mínimo pero ESTRUCTURALMENTE completo (Catálogo→Páginas→Página→
    Contenido→Fuente, con su xref) — el mismo que ya se verificó legible
    por herramientas reales en las pruebas de `describir_factura`."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]"
        b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n"
        b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"5 0 obj<</Length 44>>stream\n"
        b"BT /F1 18 Tf 10 50 Td (Hola factura) Tj ET\n"
        b"endstream\nendobj\n"
        b"xref\n0 6\n0000000000 65535 f\n"
        b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n0\n%%EOF"
    )


async def _entorno(session: AsyncSession) -> dict[str, Any]:
    await sembrar_rbac(session)
    await sembrar_estados(session)
    await sembrar_documentos(session)
    await service.sembrar_limites(session)

    empresa = (
        await session.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"),
            {"n": f"Archivado docs {uuid.uuid4().hex[:6]} S.A."},
        )
    ).scalar_one()
    usuario = (
        await session.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e,'h','N','A','ACTIVE') RETURNING id
            """),
            {"e": f"archdoc-{uuid.uuid4().hex[:10]}@amvarmar.com"},
        )
    ).scalar_one()
    origen = (
        await session.execute(
            text("""
                INSERT INTO locations (country_code, city_code, location_code, name)
                VALUES ('US','MIA','US-MIA','Miami')
                ON CONFLICT (location_code) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
            """)
        )
    ).scalar_one()
    destino = (
        await session.execute(
            text("""
                INSERT INTO locations (country_code, city_code, location_code, name)
                VALUES ('CR','SJO','CR-SJO','San José')
                ON CONFLICT (location_code) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
            """)
        )
    ).scalar_one()
    tipo_factura = (
        await session.execute(
            text("SELECT id FROM document_types WHERE code = 'COMMERCIAL_INVOICE'")
        )
    ).scalar_one()

    return {
        "empresa": empresa,
        "usuario": usuario,
        "origen": origen,
        "destino": destino,
        "tipo_factura": tipo_factura,
    }


async def _carga_delivered(session: AsyncSession, ctx: dict[str, Any]) -> uuid.UUID:
    """Todavía NO archivada — la subida de documentos (ADR-0007) rechaza
    cualquier carga con `archived_at` puesto, así que el documento se sube
    ANTES de archivar, y `_archivar` se llama después."""
    shipment_id = (
        await session.execute(
            text("""
                INSERT INTO shipments
                    (company_id, created_by, current_status_code,
                     origin_location_id, destination_location_id)
                VALUES (:c,:u,'DELIVERED',:o,:d)
                RETURNING id
            """),
            {"c": ctx["empresa"], "u": ctx["usuario"], "o": ctx["origen"], "d": ctx["destino"]},
        )
    ).scalar_one()
    await sembrar_pieza(session, shipment_id)
    return shipment_id


async def _archivar(session: AsyncSession, shipment_id: uuid.UUID) -> None:
    await session.execute(
        text("UPDATE shipments SET archived_at = now() WHERE id = :id"), {"id": shipment_id}
    )


async def _subir_documento(
    session: AsyncSession,
    ctx: dict[str, Any],
    shipment_id: uuid.UUID,
    bucket: str,
    *,
    contenido: bytes,
    original_name: str,
    is_digitally_signed: bool = False,
    media_type_forzado: str | None = None,
) -> uuid.UUID:
    """`original_name` debe coincidir con los magic bytes reales de `contenido`
    — la subida valida contenido contra extensión (Paso 3.1) antes de aceptar
    nada. `media_type_forzado` simula un tipo sin recompresión (ej. CSV) sin
    subir un archivo que la validación real rechazaría de entrada."""
    permisos = PermisosEfectivos(
        user_id=ctx["usuario"],
        authz_version=0,
        permisos=(
            PermisoEfectivo(
                code=Perm.DOCUMENTS_UPLOAD_INTERNAL,
                scope_type=ScopeType.ORGANIZATION,
                company_id=ctx["empresa"],
            ),
        ),
    )
    subida = await service.preparar_subida(
        session,
        shipment_id=shipment_id,
        document_type_id=ctx["tipo_factura"],
        issued_by="PROVIDER",
        original_name=original_name,
        company_id=ctx["empresa"],
        actor_user_id=ctx["usuario"],
        permisos=permisos,
    )
    s3._cliente().put_object(Bucket=bucket, Key=subida.storage_key, Body=contenido)
    await service.completar(session, document_id=subida.document_id, company_id=ctx["empresa"])
    if is_digitally_signed:
        await session.execute(
            text("UPDATE documents SET is_digitally_signed = true WHERE id = :id"),
            {"id": subida.document_id},
        )
    if media_type_forzado:
        await session.execute(
            text("UPDATE documents SET media_type = :m WHERE id = :id"),
            {"m": media_type_forzado, "id": subida.document_id},
        )
    return subida.document_id


class TestRecompresion:
    async def test_recomprime_una_imagen_sin_firma(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        shipment_id = await _carga_delivered(session, ctx)
        contenido = _jpeg_real()
        document_id = await _subir_documento(
            session,
            ctx,
            shipment_id,
            storage_de_prueba,
            contenido=contenido,
            original_name="factura.jpg",
        )
        await _archivar(session, shipment_id)

        total = await archivado.recomprimir_pendientes(session)

        assert total == 1
        fila = (
            await session.execute(
                text(
                    "SELECT archived_compressed_at, original_size_bytes, size_bytes, storage_key "
                    "FROM documents WHERE id = :id"
                ),
                {"id": document_id},
            )
        ).one()
        assert fila.archived_compressed_at is not None
        assert fila.original_size_bytes == len(contenido)
        # El archivo re-codificado sigue siendo una imagen real, abrible.
        objeto = s3._cliente().get_object(Bucket=storage_de_prueba, Key=fila.storage_key)
        with Image.open(BytesIO(objeto["Body"].read())) as reabierta:
            assert reabierta.size == (64, 64)

    async def test_comprime_el_contenedor_de_un_pdf_firmado_sin_tocar_el_contenido(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        shipment_id = await _carga_delivered(session, ctx)
        contenido = _pdf_real()
        document_id = await _subir_documento(
            session,
            ctx,
            shipment_id,
            storage_de_prueba,
            contenido=contenido,
            original_name="factura.pdf",
            is_digitally_signed=True,
        )
        await _archivar(session, shipment_id)

        total = await archivado.recomprimir_pendientes(session)

        assert total == 1
        fila = (
            await session.execute(
                text("SELECT storage_key, size_bytes FROM documents WHERE id = :id"),
                {"id": document_id},
            )
        ).one()
        objeto = s3._cliente().get_object(Bucket=storage_de_prueba, Key=fila.storage_key)
        assert objeto.get("ContentEncoding") == "gzip"
        # `Body.read()` con `ContentEncoding: gzip` en boto3 NO descomprime
        # solo (a diferencia de un browser) — se descomprime a mano para
        # verificar bit-a-bit contra el original.
        import gzip as gzip_mod

        assert gzip_mod.decompress(objeto["Body"].read()) == contenido

    @pytest.mark.skipif(_SIN_GHOSTSCRIPT, reason="Ghostscript no instalado")
    async def test_recomprime_un_pdf_sin_firma_con_ghostscript(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        shipment_id = await _carga_delivered(session, ctx)
        contenido = _pdf_real()
        document_id = await _subir_documento(
            session,
            ctx,
            shipment_id,
            storage_de_prueba,
            contenido=contenido,
            original_name="factura.pdf",
        )
        await _archivar(session, shipment_id)

        total = await archivado.recomprimir_pendientes(session)

        assert total == 1
        fila = (
            await session.execute(
                text("SELECT storage_key FROM documents WHERE id = :id"), {"id": document_id}
            )
        ).one()
        objeto = s3._cliente().get_object(Bucket=storage_de_prueba, Key=fila.storage_key)
        salida = objeto["Body"].read()
        assert salida.startswith(b"%PDF-")

    async def test_formato_sin_herramienta_se_marca_sin_cambiar_el_archivo(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        """CSV no es un formato que la subida acepte para una factura — se sube
        un PDF real (pasa la validación) y se fuerza `media_type` después, para
        simular un tipo de contenido sin herramienta de recompresión."""
        ctx = await _entorno(session)
        shipment_id = await _carga_delivered(session, ctx)
        contenido = _pdf_real()
        document_id = await _subir_documento(
            session,
            ctx,
            shipment_id,
            storage_de_prueba,
            contenido=contenido,
            original_name="factura.pdf",
            media_type_forzado="text/csv",
        )
        await _archivar(session, shipment_id)

        total = await archivado.recomprimir_pendientes(session)

        assert total == 1
        fila = (
            await session.execute(
                text(
                    "SELECT archived_compressed_at, original_size_bytes, size_bytes "
                    "FROM documents WHERE id = :id"
                ),
                {"id": document_id},
            )
        ).one()
        assert fila.archived_compressed_at is not None
        assert fila.original_size_bytes == len(contenido)
        assert fila.size_bytes == len(contenido)

    async def test_no_toca_documentos_de_una_carga_no_archivada(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        shipment_id = (
            await session.execute(
                text("""
                    INSERT INTO shipments
                        (company_id, created_by, current_status_code,
                         origin_location_id, destination_location_id)
                    VALUES (:c,:u,'STORED',:o,:d)
                    RETURNING id
                """),
                {"c": ctx["empresa"], "u": ctx["usuario"], "o": ctx["origen"], "d": ctx["destino"]},
            )
        ).scalar_one()
        await sembrar_pieza(session, shipment_id)
        await _subir_documento(
            session,
            ctx,
            shipment_id,
            storage_de_prueba,
            contenido=_pdf_real(),
            original_name="factura.pdf",
        )

        total = await archivado.recomprimir_pendientes(session)

        assert total == 0

    async def test_no_admite_documentos_nuevos_en_una_carga_ya_archivada(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        from app.modules.shipments.service import CargaArchivada

        ctx = await _entorno(session)
        shipment_id = await _carga_delivered(session, ctx)
        await _archivar(session, shipment_id)

        with pytest.raises(CargaArchivada):
            await _subir_documento(
                session,
                ctx,
                shipment_id,
                storage_de_prueba,
                contenido=_pdf_real(),
                original_name="factura.pdf",
            )

    async def test_no_recomprime_dos_veces(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        shipment_id = await _carga_delivered(session, ctx)
        # JPEG, no PDF: esta prueba es sobre idempotencia, no sobre
        # Ghostscript — no debe depender de que esté instalado.
        await _subir_documento(
            session,
            ctx,
            shipment_id,
            storage_de_prueba,
            contenido=_jpeg_real(),
            original_name="factura.jpg",
        )
        await _archivar(session, shipment_id)

        primera = await archivado.recomprimir_pendientes(session)
        segunda = await archivado.recomprimir_pendientes(session)

        assert primera == 1
        assert segunda == 0

    async def test_registra_auditoria_con_tamanos(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        shipment_id = await _carga_delivered(session, ctx)
        contenido = _jpeg_real()
        document_id = await _subir_documento(
            session,
            ctx,
            shipment_id,
            storage_de_prueba,
            contenido=contenido,
            original_name="factura.jpg",
        )
        await _archivar(session, shipment_id)

        await archivado.recomprimir_pendientes(session)

        fila = (
            await session.execute(
                text("""
                    SELECT actor_user_id, after_data FROM audit_logs
                    WHERE action = 'document.archived_compressed' AND resource_id = :id
                """),
                {"id": document_id},
            )
        ).one()
        assert fila.actor_user_id is None
        assert fila.after_data["shipment_id"] == str(shipment_id)
        assert fila.after_data["size_bytes_antes"] == len(contenido)
