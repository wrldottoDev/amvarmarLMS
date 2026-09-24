"""Subida de los archivos del sistema viejo al storage privado (Paso 5.3).

El gate del paso: cero archivos faltantes y cero discrepancias sin explicar. Lo
que estas pruebas vigilan es que el script sepa detectar ambas cosas, porque un
archivo que no llegó no se nota hasta que alguien lo necesita — y para entonces
el servidor viejo puede estar apagado.
"""

import hashlib
import uuid
from pathlib import Path

import pytest
from scripts import migrate_files
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.storage import s3

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def pdf(contenido: bytes = b"contenido de prueba") -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<</Type/Catalog>>\nendobj\n" + contenido + b"\ntrailer\n%%EOF\n"


@pytest.fixture
async def documento_legacy(db_directa: AsyncSession, storage_de_prueba: str, tmp_path: Path):
    """Un documento registrado con ruta del legacy y su archivo en disco."""
    marca = uuid.uuid4().hex[:8]

    empresa = (
        await db_directa.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"),
            {"n": f"Archivos {marca} S.A."},
        )
    ).scalar_one()
    usuario = (
        await db_directa.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e,'h','N','A','ACTIVE') RETURNING id
            """),
            {"e": f"f-{marca}@pruebas.amvarmar.com"},
        )
    ).scalar_one()

    media = tmp_path / "media"
    (media / "warehouse_docs" / "2026" / "01").mkdir(parents=True)
    ruta_relativa = "warehouse_docs/2026/01/factura.pdf"
    contenido = pdf()
    (media / ruta_relativa).write_bytes(contenido)

    documento = (
        await db_directa.execute(
            text("""
                INSERT INTO documents
                    (company_id, uploaded_by, storage_provider, storage_key,
                     original_name, safe_name, media_type, size_bytes, sha256,
                     upload_status, issued_by)
                VALUES (:c, :u, 'legacy', :ruta, 'factura.pdf', 'factura.pdf',
                        'application/octet-stream', :tam, :hash, 'UPLOADING', 'OTHER')
                RETURNING id
            """),
            {
                "c": empresa,
                "u": usuario,
                "ruta": ruta_relativa,
                "tam": len(contenido),
                "hash": "0" * 64,
            },
        )
    ).scalar_one()
    await db_directa.commit()

    yield {
        "id": documento,
        "empresa": empresa,
        "usuario": usuario,
        "media": media,
        "contenido": contenido,
        "ruta": ruta_relativa,
    }

    await db_directa.execute(text("DELETE FROM documents WHERE company_id = :c"), {"c": empresa})
    await db_directa.execute(text("DELETE FROM users WHERE id = :u"), {"u": usuario})
    await db_directa.execute(text("DELETE FROM companies WHERE id = :c"), {"c": empresa})
    await db_directa.commit()


class TestSubida:
    async def test_sube_el_archivo_y_guarda_su_hash_real(
        self, db_directa: AsyncSession, documento_legacy: dict
    ) -> None:
        reporte = await migrate_files.subir(
            db_directa, media_root=documento_legacy["media"], seco=False
        )

        assert reporte.subidos == 1
        assert not reporte.faltantes

        fila = (
            await db_directa.execute(
                text("""
                    SELECT storage_provider, storage_key, sha256, size_bytes,
                           media_type, upload_status
                    FROM documents WHERE id = :id
                """),
                {"id": documento_legacy["id"]},
            )
        ).one()

        assert fila.storage_provider == "s3"
        # El hash sale del archivo, no del metadato del legacy.
        assert fila.sha256 == hashlib.sha256(documento_legacy["contenido"]).hexdigest()
        assert fila.size_bytes == len(documento_legacy["contenido"])
        assert fila.media_type == "application/pdf"

    async def test_quedan_descargables(
        self, db_directa: AsyncSession, documento_legacy: dict
    ) -> None:
        """Sin antivirus, un archivo migrado se puede abrir apenas se sube.

        Antes quedaban pendientes de escaneo y por lo tanto invisibles. El
        antivirus se retiró por decisión de AMVARMAR (enmienda de ADR-0009), así
        que dejarlos sin marcar los volvería inaccesibles para siempre: el
        worker que los movía ya no existe.
        """
        await migrate_files.subir(db_directa, media_root=documento_legacy["media"], seco=False)

        estado = (
            await db_directa.execute(
                text("SELECT upload_status FROM documents WHERE id = :id"),
                {"id": documento_legacy["id"]},
            )
        ).scalar_one()

        assert estado == "READY"

    async def test_el_objeto_existe_de_verdad_en_el_storage(
        self, db_directa: AsyncSession, documento_legacy: dict
    ) -> None:
        await migrate_files.subir(db_directa, media_root=documento_legacy["media"], seco=False)

        clave = (
            await db_directa.execute(
                text("SELECT storage_key FROM documents WHERE id = :id"),
                {"id": documento_legacy["id"]},
            )
        ).scalar_one()

        objeto = await s3.describir_objeto(clave)
        assert objeto.size_bytes == len(documento_legacy["contenido"])

    async def test_el_dry_run_no_escribe_nada(
        self, db_directa: AsyncSession, documento_legacy: dict
    ) -> None:
        reporte = await migrate_files.subir(
            db_directa, media_root=documento_legacy["media"], seco=True
        )

        assert reporte.subidos == 1
        proveedor = (
            await db_directa.execute(
                text("SELECT storage_provider FROM documents WHERE id = :id"),
                {"id": documento_legacy["id"]},
            )
        ).scalar_one()
        assert proveedor == "legacy"

    async def test_correrlo_dos_veces_no_vuelve_a_subir(
        self, db_directa: AsyncSession, documento_legacy: dict
    ) -> None:
        """Son casi 10 GB: cortarlo y reanudarlo es lo normal, no la excepción."""
        await migrate_files.subir(db_directa, media_root=documento_legacy["media"], seco=False)

        segunda = await migrate_files.subir(
            db_directa, media_root=documento_legacy["media"], seco=False
        )

        assert segunda.subidos == 0

    async def test_un_archivo_que_falta_se_reporta_y_no_frena_el_resto(
        self, db_directa: AsyncSession, documento_legacy: dict
    ) -> None:
        await db_directa.execute(
            text("""
                INSERT INTO documents
                    (company_id, uploaded_by, storage_provider, storage_key,
                     original_name, safe_name, media_type, size_bytes, sha256,
                     upload_status, issued_by)
                VALUES (:c, :u, 'legacy', 'warehouse_docs/2026/01/no-existe.pdf',
                        'no-existe.pdf', 'no-existe.pdf', 'application/pdf', 10,
                        :hash, 'UPLOADING', 'OTHER')
            """),
            {"c": documento_legacy["empresa"], "u": documento_legacy["usuario"], "hash": "0" * 64},
        )
        await db_directa.commit()

        reporte = await migrate_files.subir(
            db_directa, media_root=documento_legacy["media"], seco=False
        )

        assert reporte.subidos == 1
        assert reporte.faltantes == ["warehouse_docs/2026/01/no-existe.pdf"]

    async def test_una_ruta_que_se_escapa_de_media_se_rechaza(
        self, db_directa: AsyncSession, documento_legacy: dict
    ) -> None:
        """La ruta viene de la base; si alguien la manipulara, `..` haría leer
        fuera de `media/`."""
        await db_directa.execute(
            text("""
                UPDATE documents SET storage_key = '../../etc/passwd' WHERE id = :id
            """),
            {"id": documento_legacy["id"]},
        )
        await db_directa.commit()

        reporte = await migrate_files.subir(
            db_directa, media_root=documento_legacy["media"], seco=False
        )

        assert reporte.subidos == 0
        assert "fuera de media/" in reporte.faltantes[0]

    async def test_un_tamano_distinto_al_del_legacy_se_anota(
        self, db_directa: AsyncSession, documento_legacy: dict
    ) -> None:
        """El archivo es la verdad; el metadato viejo puede estar desactualizado.
        Se sube igual y la diferencia queda listada."""
        await db_directa.execute(
            text("UPDATE documents SET size_bytes = 99999 WHERE id = :id"),
            {"id": documento_legacy["id"]},
        )
        await db_directa.commit()

        reporte = await migrate_files.subir(
            db_directa, media_root=documento_legacy["media"], seco=False
        )

        assert reporte.subidos == 1
        assert len(reporte.discrepancias) == 1
        assert "99999" in reporte.discrepancias[0]


class TestVerificacion:
    async def test_relee_del_storage_y_compara_el_hash(
        self, db_directa: AsyncSession, documento_legacy: dict
    ) -> None:
        """Uno por uno, no por muestreo: comparar la base contra sí misma no
        probaría que el archivo llegó."""
        await migrate_files.subir(db_directa, media_root=documento_legacy["media"], seco=False)

        reporte = await migrate_files.verificar(db_directa)

        assert reporte.subidos >= 1
        assert not reporte.faltantes
        assert not reporte.discrepancias

    async def test_detecta_un_hash_que_no_corresponde(
        self, db_directa: AsyncSession, documento_legacy: dict
    ) -> None:
        await migrate_files.subir(db_directa, media_root=documento_legacy["media"], seco=False)
        await db_directa.execute(
            text("UPDATE documents SET sha256 = :h WHERE id = :id"),
            {"h": "f" * 64, "id": documento_legacy["id"]},
        )
        await db_directa.commit()

        reporte = await migrate_files.verificar(db_directa)

        assert any("hash" in d for d in reporte.discrepancias)

    async def test_detecta_un_objeto_que_no_esta(
        self, db_directa: AsyncSession, documento_legacy: dict
    ) -> None:
        await migrate_files.subir(db_directa, media_root=documento_legacy["media"], seco=False)
        clave = (
            await db_directa.execute(
                text("SELECT storage_key FROM documents WHERE id = :id"),
                {"id": documento_legacy["id"]},
            )
        ).scalar_one()
        await s3.eliminar(clave)

        reporte = await migrate_files.verificar(db_directa)

        assert any("no está en el storage" in f for f in reporte.faltantes)
