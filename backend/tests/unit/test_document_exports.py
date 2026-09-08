"""ZIP documentales: escritura secuencial, nombres y fallos de storage."""

import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest

from app.infrastructure.storage import s3
from app.modules.documents import exports

pytestmark = pytest.mark.unit


def _plan(*archivos: exports.ArchivoFuente) -> exports.PlanExportacion:
    return exports.PlanExportacion(
        job_id=uuid4(),
        company_id=uuid4(),
        context="SHIPMENT",
        resource_id=uuid4(),
        kind="ALL_DOCUMENTS",
        result_name="SHP-TEST-documentos.zip",
        storage_key="exports/test.zip",
        archivos=list(archivos),
    )


def _archivo(*, key: str, nombre: str = "factura.pdf") -> exports.ArchivoFuente:
    return exports.ArchivoFuente(
        id=uuid4(),
        storage_key=key,
        original_name=nombre,
        safe_name=nombre,
        upload_status="READY",
        sha256="a" * 64,
        tipo="COMMERCIAL_INVOICE",
    )


async def test_zip_grande_se_escribe_por_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cantidad = 128
    tamano_chunk = 64 * 1024
    chunks_leidos = 0

    async def chunks(_storage_key: str) -> AsyncIterator[bytes]:
        nonlocal chunks_leidos
        for _ in range(cantidad):
            chunks_leidos += 1
            yield b"x" * tamano_chunk

    monkeypatch.setattr(s3, "iterar_chunks", chunks)
    destino = tmp_path / "expediente.zip"

    await exports._construir_zip(_plan(_archivo(key="grande")), destino)

    assert chunks_leidos == cantidad
    with zipfile.ZipFile(destino) as paquete:
        assert paquete.infolist()[0].file_size == cantidad * tamano_chunk


async def test_nombres_repetidos_no_se_pisan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def chunks(storage_key: str) -> AsyncIterator[bytes]:
        yield storage_key.encode()

    monkeypatch.setattr(s3, "iterar_chunks", chunks)
    destino = tmp_path / "repetidos.zip"

    await exports._construir_zip(_plan(_archivo(key="uno"), _archivo(key="dos")), destino)

    with zipfile.ZipFile(destino) as paquete:
        assert paquete.namelist() == [
            "COMMERCIAL_INVOICE/factura.pdf",
            "COMMERCIAL_INVOICE/factura-2.pdf",
        ]
        assert paquete.read(paquete.namelist()[0]) == b"uno"
        assert paquete.read(paquete.namelist()[1]) == b"dos"


async def test_objeto_faltante_falla_el_job_en_vez_de_crear_zip_incompleto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def chunks(storage_key: str) -> AsyncIterator[bytes]:
        raise s3.ObjetoNoEncontrado(storage_key)
        yield b""  # pragma: no cover - convierte la función en generador async

    monkeypatch.setattr(s3, "iterar_chunks", chunks)

    with pytest.raises(s3.ObjetoNoEncontrado):
        await exports._construir_zip(
            _plan(_archivo(key="ya-no-existe")), tmp_path / "incompleto.zip"
        )
