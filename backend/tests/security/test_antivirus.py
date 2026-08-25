"""Escaneo antivirus (Paso 3.2).

La mayoría de los tests corren contra un ClamAV falso que habla el mismo
protocolo de cable. Eso ejercita el código real —parseo de la respuesta, cambio
de estado, auditoría— sin esperar el minuto largo que tarda un ClamAV de verdad
en cargar sus firmas.

Hay además un test contra ClamAV real, marcado `slow`, que verifica que el
protocolo implementado a mano es efectivamente el que habla ClamAV. Sin él,
todo lo demás probaría solo contra mis propias suposiciones.
"""

import asyncio
import struct
import uuid
from collections.abc import AsyncGenerator, Callable

import pytest
from scripts.seed_document_types import sembrar as sembrar_documentos
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.infrastructure.antivirus import clamav
from app.infrastructure.storage import s3
from app.modules.documents import service
from app.modules.documents.models import ScanStatus, UploadStatus
from app.workers.tasks import scan

pytestmark = pytest.mark.security


# La firma EICAR es el archivo de prueba estándar que todo antivirus detecta.
# Se arma por partes a propósito: escrita literal, el antivirus del equipo de
# desarrollo pondría en cuarentena este mismo archivo de tests.
def eicar() -> bytes:
    return b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" + b"!$H+H*"


def pdf_real() -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<</Type/Catalog>>\nendobj\ntrailer\n%%EOF\n"


def pdf_con_eicar() -> bytes:
    """Un PDF válido con la firma EICAR adentro.

    Es el caso que justifica el antivirus. Un EICAR desnudo ni siquiera llega
    hasta acá: la validación de subida lo rechaza porque no es un PDF. Lo que
    el escáner atrapa es un documento legítimo en forma, con contenido hostil
    adentro.
    """
    return b"%PDF-1.4\n1 0 obj\n<</Type/Catalog>>\nendobj\n" + eicar() + b"\ntrailer\n%%EOF\n"


# --- ClamAV falso ---


@pytest.fixture
async def clamav_falso(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[Callable[[bytes | None], None]]:
    """Servidor que habla INSTREAM y responde lo que el test le indique.

    Devuelve una función para fijar la respuesta: `None` corta la conexión sin
    responder, que es como se ve un escáner que se cae a mitad del escaneo.
    """
    respuesta: dict[str, bytes | None] = {"valor": b"stream: OK\0"}

    async def manejar(lector: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        # Consumir el comando y el flujo completo antes de responder, igual que
        # ClamAV: responder antes haría que el test pase por el motivo
        # equivocado.
        await lector.readuntil(b"\0")
        while True:
            cabecera = await lector.readexactly(4)
            (largo,) = struct.unpack("!L", cabecera)
            if largo == 0:
                break
            await lector.readexactly(largo)

        if respuesta["valor"] is not None:
            escritor.write(respuesta["valor"])
            await escritor.drain()
        escritor.close()

    servidor = await asyncio.start_server(manejar, "127.0.0.1", 0)
    puerto = servidor.sockets[0].getsockname()[1]

    monkeypatch.setenv("CLAMAV_HOST", "127.0.0.1")
    monkeypatch.setenv("CLAMAV_PORT", str(puerto))
    get_settings.cache_clear()

    def responder_con(valor: bytes | None) -> None:
        respuesta["valor"] = valor

    async with servidor:
        yield responder_con

    get_settings.cache_clear()


class TestProtocolo:
    async def test_un_archivo_limpio_da_limpio(self, clamav_falso) -> None:
        clamav_falso(b"stream: OK\0")

        veredicto = await clamav.escanear(pdf_real())

        assert veredicto.resultado is clamav.ResultadoEscaneo.LIMPIO
        assert veredicto.amenaza is None

    async def test_una_firma_encontrada_da_infectado_con_su_nombre(self, clamav_falso) -> None:
        """El nombre de la amenaza se guarda para poder investigar un falso
        positivo sin volver a escanear."""
        clamav_falso(b"stream: Win.Test.EICAR_HDB-1 FOUND\0")

        veredicto = await clamav.escanear(eicar())

        assert veredicto.resultado is clamav.ResultadoEscaneo.INFECTADO
        assert veredicto.amenaza == "Win.Test.EICAR_HDB-1"

    async def test_un_error_del_escaner_no_se_toma_como_limpio(self, clamav_falso) -> None:
        """Tratar un ERROR como limpio sería exactamente el fail open a evitar."""
        clamav_falso(b"stream: size limit exceeded ERROR\0")

        with pytest.raises(clamav.EscanerNoDisponible):
            await clamav.escanear(pdf_real())

    async def test_una_respuesta_incomprensible_no_se_toma_como_limpia(self, clamav_falso) -> None:
        clamav_falso(b"algo totalmente inesperado\0")

        with pytest.raises(clamav.EscanerNoDisponible):
            await clamav.escanear(pdf_real())

    async def test_si_el_escaner_corta_sin_responder_falla(self, clamav_falso) -> None:
        clamav_falso(None)

        with pytest.raises(clamav.EscanerNoDisponible):
            await clamav.escanear(pdf_real())

    async def test_si_el_escaner_no_existe_falla(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Puerto cerrado: el caso del contenedor caído."""
        monkeypatch.setenv("CLAMAV_HOST", "127.0.0.1")
        # Puerto reservado por IANA como "descartar", nunca hay nada escuchando.
        monkeypatch.setenv("CLAMAV_PORT", "9")
        get_settings.cache_clear()

        try:
            with pytest.raises(clamav.EscanerNoDisponible):
                await clamav.escanear(pdf_real())
        finally:
            get_settings.cache_clear()

    async def test_un_archivo_grande_se_envia_en_trozos(self, clamav_falso) -> None:
        """El troceado mantiene la memoria plana sin importar el tamaño."""
        clamav_falso(b"stream: OK\0")

        veredicto = await clamav.escanear(b"A" * (300 * 1024))

        assert veredicto.resultado is clamav.ResultadoEscaneo.LIMPIO


# --- Efecto sobre el documento ---


async def _documento_pendiente(
    session: AsyncSession, bucket: str, contenido: bytes
) -> tuple[uuid.UUID, uuid.UUID]:
    """Deja un documento en PROCESSING/PENDING, listo para escanear."""
    await sembrar_rbac(session)
    await sembrar_estados(session)
    await sembrar_documentos(session)
    await service.sembrar_limites(session)

    empresa = (
        await session.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"),
            {"n": f"AV {uuid.uuid4().hex[:6]} S.A."},
        )
    ).scalar_one()
    usuario = (
        await session.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e,'h','N','A','ACTIVE') RETURNING id
            """),
            {"e": f"av-{uuid.uuid4().hex[:8]}@amvarmar.com"},
        )
    ).scalar_one()
    origen = await _ubicacion(session, "US", "MIA", "Miami")
    destino = await _ubicacion(session, "CR", "SJO", "San José")
    shipment = (
        await session.execute(
            text("""
                INSERT INTO shipments
                    (company_id, created_by, current_status_code,
                     origin_location_id, destination_location_id)
                VALUES (:c,:u,'IN_TRANSIT',:o,:d) RETURNING id
            """),
            {"c": empresa, "u": usuario, "o": origen, "d": destino},
        )
    ).scalar_one()
    tipo = (
        await session.execute(
            text("SELECT id FROM document_types WHERE code = 'COMMERCIAL_INVOICE'")
        )
    ).scalar_one()

    subida = await service.preparar_subida(
        session,
        shipment_id=shipment,
        document_type_id=tipo,
        original_name="factura.pdf",
        company_id=empresa,
        actor_user_id=usuario,
    )
    s3._cliente().put_object(Bucket=bucket, Key=subida.storage_key, Body=contenido)
    await service.completar(session, document_id=subida.document_id, company_id=empresa)

    return subida.document_id, empresa


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


async def _estado(session: AsyncSession, document_id: uuid.UUID):
    return (
        await session.execute(
            text("""
                SELECT scan_status, upload_status, scanned_at
                FROM documents WHERE id = :id
            """),
            {"id": document_id},
        )
    ).one()


class TestEfectoSobreElDocumento:
    async def test_un_archivo_limpio_queda_descargable(
        self, session: AsyncSession, storage_de_prueba: str, clamav_falso
    ) -> None:
        clamav_falso(b"stream: OK\0")
        doc, empresa = await _documento_pendiente(session, storage_de_prueba, pdf_real())

        resultado = await scan.escanear_documento(session, doc)

        assert resultado == ScanStatus.CLEAN
        estado = await _estado(session, doc)
        assert estado.scan_status == ScanStatus.CLEAN
        # Recién ahora es descargable.
        assert estado.upload_status == UploadStatus.READY
        assert estado.scanned_at is not None

        descarga = await service.preparar_descarga(session, document_id=doc, company_ids=[empresa])
        assert descarga.url.startswith("http")

    async def test_un_archivo_infectado_queda_en_cuarentena(
        self, session: AsyncSession, storage_de_prueba: str, clamav_falso
    ) -> None:
        """Parte del gate: un documento marcado infectado no se descarga.

        El escáner es falso a propósito: lo que se verifica es la máquina de
        estados, no la detección. Que ClamAV real detecte EICAR se prueba
        aparte, en `TestContraClamAVReal`.
        """
        clamav_falso(b"stream: Eicar-Signature FOUND\0")
        doc, empresa = await _documento_pendiente(session, storage_de_prueba, pdf_con_eicar())

        resultado = await scan.escanear_documento(session, doc)

        assert resultado == ScanStatus.INFECTED
        estado = await _estado(session, doc)
        assert estado.scan_status == ScanStatus.INFECTED
        assert estado.upload_status == UploadStatus.FAILED

        with pytest.raises(service.DocumentoEnCuarentena):
            await service.preparar_descarga(session, document_id=doc, company_ids=[empresa])

    async def test_un_archivo_infectado_no_se_borra(
        self, session: AsyncSession, storage_de_prueba: str, clamav_falso
    ) -> None:
        """Borrarlo eliminaría la evidencia del incidente."""
        clamav_falso(b"stream: Eicar-Signature FOUND\0")
        doc, _ = await _documento_pendiente(session, storage_de_prueba, pdf_con_eicar())
        clave = (
            await session.execute(
                text("SELECT storage_key FROM documents WHERE id = :id"), {"id": doc}
            )
        ).scalar_one()

        await scan.escanear_documento(session, doc)

        # Sigue en el bucket.
        assert (await s3.describir_objeto(clave)).size_bytes > 0

    async def test_la_amenaza_queda_auditada(
        self, session: AsyncSession, storage_de_prueba: str, clamav_falso
    ) -> None:
        clamav_falso(b"stream: Eicar-Signature FOUND\0")
        doc, _ = await _documento_pendiente(session, storage_de_prueba, pdf_con_eicar())

        await scan.escanear_documento(session, doc)

        fila = (
            await session.execute(
                text("""
                    SELECT action, outcome, reason, after_data
                    FROM audit_logs WHERE resource_id = :id AND action LIKE 'document.scan%'
                """),
                {"id": doc},
            )
        ).one()
        assert fila.action == "document.scan.infected"
        assert fila.outcome == "DENIED"
        assert "Eicar-Signature" in fila.reason
        assert fila.after_data["amenaza"] == "Eicar-Signature"


class TestFailClosed:
    """La otra mitad del gate.

    Un escáner que no funciona no puede autorizar nada. En todos estos casos el
    documento se queda pendiente y NO se vuelve descargable.
    """

    async def test_escaner_caido_deja_el_documento_pendiente(
        self,
        session: AsyncSession,
        storage_de_prueba: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        doc, empresa = await _documento_pendiente(session, storage_de_prueba, pdf_real())
        monkeypatch.setenv("CLAMAV_HOST", "127.0.0.1")
        monkeypatch.setenv("CLAMAV_PORT", "9")
        get_settings.cache_clear()

        try:
            resultado = await scan.escanear_documento(session, doc)
        finally:
            get_settings.cache_clear()

        assert resultado == ScanStatus.PENDING
        estado = await _estado(session, doc)
        assert estado.scan_status == ScanStatus.PENDING
        # Sigue sin ser descargable: no pasó a READY por defecto.
        assert estado.upload_status == UploadStatus.PROCESSING

        with pytest.raises(service.DocumentoNoDisponible):
            await service.preparar_descarga(session, document_id=doc, company_ids=[empresa])

    async def test_un_error_del_escaner_deja_el_documento_pendiente(
        self, session: AsyncSession, storage_de_prueba: str, clamav_falso
    ) -> None:
        clamav_falso(b"stream: something went wrong ERROR\0")
        doc, _ = await _documento_pendiente(session, storage_de_prueba, pdf_real())

        resultado = await scan.escanear_documento(session, doc)

        assert resultado == ScanStatus.PENDING
        assert (await _estado(session, doc)).upload_status == UploadStatus.PROCESSING

    async def test_un_documento_pendiente_se_reintenta(
        self, session: AsyncSession, storage_de_prueba: str, clamav_falso
    ) -> None:
        """Queda en la cola: el siguiente pase lo vuelve a tomar."""
        clamav_falso(b"stream: caido ERROR\0")
        doc, _ = await _documento_pendiente(session, storage_de_prueba, pdf_real())
        await scan.escanear_documento(session, doc)

        # El escáner se recupera.
        clamav_falso(b"stream: OK\0")
        lote = await scan.escanear_pendientes(session)

        assert lote.limpios >= 1
        assert (await _estado(session, doc)).scan_status == ScanStatus.CLEAN


class TestLote:
    async def test_procesa_varios_documentos(
        self, session: AsyncSession, storage_de_prueba: str, clamav_falso
    ) -> None:
        clamav_falso(b"stream: OK\0")
        for _ in range(3):
            await _documento_pendiente(session, storage_de_prueba, pdf_real())

        lote = await scan.escanear_pendientes(session)

        assert lote.limpios == 3
        assert lote.infectados == 0

    async def test_no_toca_documentos_ya_escaneados(
        self, session: AsyncSession, storage_de_prueba: str, clamav_falso
    ) -> None:
        clamav_falso(b"stream: OK\0")
        doc, _ = await _documento_pendiente(session, storage_de_prueba, pdf_real())
        await scan.escanear_pendientes(session)
        primera_vez = (await _estado(session, doc)).scanned_at

        segundo_lote = await scan.escanear_pendientes(session)

        assert segundo_lote.limpios == 0
        assert (await _estado(session, doc)).scanned_at == primera_vez


@pytest.mark.slow
class TestContraClamAVReal:
    """Verifica que el protocolo implementado a mano es el que habla ClamAV.

    Sin esto, todo lo anterior probaría solo contra mis suposiciones sobre el
    formato de cable. Marcado `slow` porque ClamAV tarda cerca de un minuto en
    cargar sus firmas; se excluye con `-m "not slow"`.
    """

    async def test_detecta_eicar_de_verdad(self) -> None:
        if not await clamav.esta_disponible():
            pytest.skip("ClamAV no está corriendo (docker compose up clamav).")

        veredicto = await clamav.escanear(eicar())

        assert veredicto.resultado is clamav.ResultadoEscaneo.INFECTADO
        assert veredicto.amenaza is not None
        assert "eicar" in veredicto.amenaza.lower()

    async def test_eicar_embebido_en_un_pdf_no_dispara_la_firma(self) -> None:
        """Documenta un límite real de EICAR, no un defecto del sistema.

        La firma EICAR coincide con el archivo completo; ClamAV no la busca
        dentro de otro formato. Por eso los tests del flujo de cuarentena usan
        un escáner falso: lo que ahí se verifica es la máquina de estados
        (infectado -> cuarentena -> no descargable), no la detección.

        Con malware real la detección sí opera dentro de documentos; EICAR es
        solo un archivo de prueba, no una muestra representativa.
        """
        if not await clamav.esta_disponible():
            pytest.skip("ClamAV no está corriendo (docker compose up clamav).")

        veredicto = await clamav.escanear(pdf_con_eicar())

        assert veredicto.resultado is clamav.ResultadoEscaneo.LIMPIO

    async def test_un_pdf_normal_pasa(self) -> None:
        if not await clamav.esta_disponible():
            pytest.skip("ClamAV no está corriendo (docker compose up clamav).")

        veredicto = await clamav.escanear(pdf_real())

        assert veredicto.resultado is clamav.ResultadoEscaneo.LIMPIO
