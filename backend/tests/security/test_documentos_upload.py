"""Seguridad de la subida de documentos (Paso 3.1).

Los 8 casos del gate. Todos verifican lo mismo desde ángulos distintos: **el
servidor no confía en lo que declara el cliente**, ni en la extensión, ni en el
`Content-Type`, ni en el tamaño anunciado.
"""

import struct
import uuid
import zlib

import pytest
from scripts.seed_document_types import sembrar as sembrar_documentos
from scripts.seed_rbac import sembrar as sembrar_rbac
from scripts.seed_shipment_statuses import sembrar as sembrar_estados
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.storage import s3
from app.modules.documents import service
from app.modules.documents.models import ScanStatus, UploadStatus
from app.modules.documents.validation import (
    ArchivoDemasiadoGrande,
    ArchivoInvalido,
    ContenidoNoCoincide,
    FormatoNoPermitido,
    detectar_media_type,
    nombre_seguro,
    validar_contenido,
    validar_tamano,
)

pytestmark = pytest.mark.security


# --- Archivos de prueba con magic bytes REALES ---


def pdf_real() -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<</Type/Catalog>>\nendobj\ntrailer\n%%EOF\n"


def png_real() -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + ihdr
        + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    )


def jpeg_real() -> bytes:
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\xff\xd9"


def ejecutable_elf() -> bytes:
    """Binario ELF de 64 bits. El caso del ejecutable disfrazado."""
    return b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8 + struct.pack("<HH", 2, 0x3E) + b"\x00" * 40


_FORMATOS_FACTURA = ["PDF", "JPG", "JPEG", "PNG", "WEBP", "HEIC", "DOCX"]


class TestValidacionDeContenido:
    """Los casos que no necesitan storage: validación pura sobre los bytes."""

    def test_un_pdf_renombrado_a_jpg_se_rechaza(self) -> None:
        """Caso 1 del gate. La extensión miente; los bytes no."""
        with pytest.raises(ContenidoNoCoincide) as error:
            validar_contenido(
                cabecera=pdf_real(),
                nombre="factura.jpg",
                formatos_permitidos=_FORMATOS_FACTURA,
            )

        assert error.value.code == "CONTENIDO_NO_COINCIDE_CON_LA_EXTENSION"
        assert "application/pdf" in error.value.message

    def test_un_ejecutable_disfrazado_de_pdf_se_rechaza(self) -> None:
        """Caso 2 del gate.

        El `Content-Type: application/pdf` que manda el cliente es trivial de
        fabricar; el magic number no. Acá ni siquiera se mira el header.
        """
        with pytest.raises(FormatoNoPermitido):
            validar_contenido(
                cabecera=ejecutable_elf(),
                nombre="factura.pdf",
                formatos_permitidos=_FORMATOS_FACTURA,
            )

    def test_el_mime_se_detecta_de_los_bytes(self) -> None:
        assert detectar_media_type(pdf_real()) == "application/pdf"
        assert detectar_media_type(png_real()) == "image/png"
        assert detectar_media_type(ejecutable_elf()) == "application/x-executable"

    def test_un_archivo_vacio_se_rechaza(self) -> None:
        """Caso 3 del gate. Cero bytes nunca es un documento; suele indicar
        que la subida se cortó."""
        with pytest.raises(ArchivoInvalido) as error:
            validar_tamano(0, maximo=1000)

        assert "vacío" in error.value.message

    def test_un_archivo_sobre_el_limite_se_rechaza(self) -> None:
        """Caso 4 del gate."""
        with pytest.raises(ArchivoDemasiadoGrande):
            validar_tamano(300 * 1024 * 1024, maximo=250 * 1024 * 1024)

    @pytest.mark.parametrize(
        "nombre",
        ["cargas.zip", "backup.rar", "datos.7z", "archivo.tar", "comprimido.gz"],
    )
    def test_los_comprimidos_estan_prohibidos(self, nombre: str) -> None:
        """Caso 5 del gate. ADR-0009 prohíbe ZIP directamente en vez de
        intentar detectar bombas: sin descomprimir no hay superficie de ataque."""
        with pytest.raises(FormatoNoPermitido):
            validar_contenido(
                cabecera=pdf_real(), nombre=nombre, formatos_permitidos=["PDF", "ZIP"]
            )

    @pytest.mark.parametrize(
        "nombre",
        ["macro.docm", "hoja.xlsm", "virus.exe", "script.sh", "app.js", "run.bat"],
    )
    def test_las_extensiones_peligrosas_estan_prohibidas(self, nombre: str) -> None:
        """Bloquear por extensión es más barato y confiable que intentar
        detectar macros dentro de un Office XML (ADR-0009)."""
        with pytest.raises(FormatoNoPermitido):
            validar_contenido(
                cabecera=pdf_real(),
                nombre=nombre,
                formatos_permitidos=[*_FORMATOS_FACTURA, "DOCM", "EXE"],
            )

    def test_un_formato_valido_pero_no_permitido_para_el_tipo_se_rechaza(self) -> None:
        """Solo el packing list acepta hoja de cálculo (ADR-0009)."""
        with pytest.raises(FormatoNoPermitido) as error:
            validar_contenido(
                cabecera=png_real(), nombre="factura.png", formatos_permitidos=["PDF"]
            )

        assert "no acepta archivos .png" in error.value.message

    def test_un_archivo_correcto_pasa(self) -> None:
        resultado = validar_contenido(
            cabecera=pdf_real(),
            nombre="Factura Comercial N° 99123.pdf",
            formatos_permitidos=_FORMATOS_FACTURA,
        )

        assert resultado.media_type == "application/pdf"
        assert resultado.formato == "PDF"
        # El nombre se sanea para poder servirlo en Content-Disposition.
        assert resultado.safe_name == "Factura_Comercial_N_99123.pdf"


class TestNombreSeguro:
    @pytest.mark.parametrize(
        ("entrada", "esperado"),
        [
            ("../../etc/passwd.pdf", "passwd.pdf"),
            ("C:\\Windows\\system32\\cmd.pdf", "cmd.pdf"),
            ("factura\ninyectada.pdf", "factura_inyectada.pdf"),
            ('factura"comillas.pdf', "factura_comillas.pdf"),
            ("Facturación Ñoño.pdf", "Facturacion_Nono.pdf"),
        ],
    )
    def test_neutraliza_nombres_hostiles(self, entrada: str, esperado: str) -> None:
        """Un nombre de archivo llega del cliente: puede traer rutas, saltos de
        línea que rompan la cabecera HTTP, o comillas que la cierren antes."""
        assert nombre_seguro(entrada) == esperado

    def test_un_nombre_sin_nada_util_no_queda_vacio(self) -> None:
        assert nombre_seguro("....") == "documento"


# --- Casos que necesitan storage y base ---


async def _entorno(session: AsyncSession) -> dict[str, uuid.UUID]:
    await sembrar_rbac(session)
    await sembrar_estados(session)
    await sembrar_documentos(session)
    await service.sembrar_limites(session)

    empresa = (
        await session.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"),
            {"n": f"Docs {uuid.uuid4().hex[:6]} S.A."},
        )
    ).scalar_one()
    otra = (
        await session.execute(
            text("INSERT INTO companies (legal_name, status) VALUES (:n,'ACTIVE') RETURNING id"),
            {"n": f"Ajena {uuid.uuid4().hex[:6]} S.A."},
        )
    ).scalar_one()
    usuario = (
        await session.execute(
            text("""
                INSERT INTO users (email, password_hash, first_name, last_name, status)
                VALUES (:e,'h','N','A','ACTIVE') RETURNING id
            """),
            {"e": f"doc-{uuid.uuid4().hex[:8]}@amvarmar.com"},
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

    return {
        "empresa": empresa,
        "otra_empresa": otra,
        "usuario": usuario,
        "shipment": shipment,
        "tipo_factura": tipo,
    }


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


class TestFlujoEnDosTiempos:
    async def test_preparar_deja_el_documento_en_uploading(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        """Existe en la base pero no satisface nada hasta que se verifique."""
        ctx = await _entorno(session)

        subida = await service.preparar_subida(
            session,
            shipment_id=ctx["shipment"],
            document_type_id=ctx["tipo_factura"],
            original_name="factura.pdf",
            company_id=ctx["empresa"],
            actor_user_id=ctx["usuario"],
        )

        fila = (
            await session.execute(
                text(
                    "SELECT upload_status, scan_status, storage_key FROM documents WHERE id = :id"
                ),
                {"id": subida.document_id},
            )
        ).one()
        assert fila.upload_status == UploadStatus.UPLOADING
        assert fila.scan_status == ScanStatus.PENDING
        # La clave lleva la empresa y un sufijo aleatorio: no es adivinable a
        # partir del id del documento.
        assert str(ctx["empresa"]) in fila.storage_key
        assert subida.upload_url.startswith("http")

    async def test_una_extension_no_permitida_falla_antes_de_subir(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        """Le da el error al usuario antes de que transfiera 200 MB."""
        ctx = await _entorno(session)

        with pytest.raises(FormatoNoPermitido):
            await service.preparar_subida(
                session,
                shipment_id=ctx["shipment"],
                document_type_id=ctx["tipo_factura"],
                original_name="planilla.xlsx",
                company_id=ctx["empresa"],
                actor_user_id=ctx["usuario"],
            )

    async def test_completar_sin_haber_subido_falla(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        """Sin esto, bastaría llamar a `complete` sin subir nada para dejar un
        documento fantasma marcado como válido."""
        ctx = await _entorno(session)
        subida = await service.preparar_subida(
            session,
            shipment_id=ctx["shipment"],
            document_type_id=ctx["tipo_factura"],
            original_name="factura.pdf",
            company_id=ctx["empresa"],
            actor_user_id=ctx["usuario"],
        )

        with pytest.raises(ArchivoInvalido):
            await service.completar(
                session, document_id=subida.document_id, company_id=ctx["empresa"]
            )

        estado = (
            await session.execute(
                text("SELECT upload_status FROM documents WHERE id = :id"),
                {"id": subida.document_id},
            )
        ).scalar_one()
        assert estado == UploadStatus.FAILED

    async def test_un_archivo_valido_completa_y_calcula_su_hash(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        import hashlib

        ctx = await _entorno(session)
        subida = await service.preparar_subida(
            session,
            shipment_id=ctx["shipment"],
            document_type_id=ctx["tipo_factura"],
            original_name="factura.pdf",
            company_id=ctx["empresa"],
            actor_user_id=ctx["usuario"],
        )
        contenido = pdf_real()
        s3._cliente().put_object(Bucket=storage_de_prueba, Key=subida.storage_key, Body=contenido)

        resultado = await service.completar(
            session, document_id=subida.document_id, company_id=ctx["empresa"]
        )

        assert resultado.media_type == "application/pdf"
        assert resultado.size_bytes == len(contenido)
        assert resultado.sha256 == hashlib.sha256(contenido).hexdigest()

        estado = (
            await session.execute(
                text("SELECT upload_status FROM documents WHERE id = :id"),
                {"id": subida.document_id},
            )
        ).scalar_one()
        # PROCESSING, no READY: el antivirus corre antes (ADR-0009).
        assert estado == UploadStatus.PROCESSING

    async def test_un_ejecutable_subido_como_pdf_se_rechaza_y_se_borra(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        """El caso completo: el cliente pasó la validación de extensión al
        preparar, y sube otra cosa. La verificación sobre los bytes lo atrapa."""
        ctx = await _entorno(session)
        subida = await service.preparar_subida(
            session,
            shipment_id=ctx["shipment"],
            document_type_id=ctx["tipo_factura"],
            original_name="factura.pdf",
            company_id=ctx["empresa"],
            actor_user_id=ctx["usuario"],
        )
        s3._cliente().put_object(
            Bucket=storage_de_prueba, Key=subida.storage_key, Body=ejecutable_elf()
        )

        with pytest.raises(FormatoNoPermitido):
            await service.completar(
                session, document_id=subida.document_id, company_id=ctx["empresa"]
            )

        # Una subida rechazada no deja basura en el bucket.
        with pytest.raises(s3.ObjetoNoEncontrado):
            await s3.describir_objeto(subida.storage_key)

    async def test_no_se_puede_completar_dos_veces(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        from app.core.errors import Conflicto

        ctx = await _entorno(session)
        subida = await service.preparar_subida(
            session,
            shipment_id=ctx["shipment"],
            document_type_id=ctx["tipo_factura"],
            original_name="factura.pdf",
            company_id=ctx["empresa"],
            actor_user_id=ctx["usuario"],
        )
        s3._cliente().put_object(Bucket=storage_de_prueba, Key=subida.storage_key, Body=pdf_real())
        await service.completar(session, document_id=subida.document_id, company_id=ctx["empresa"])

        with pytest.raises(Conflicto):
            await service.completar(
                session, document_id=subida.document_id, company_id=ctx["empresa"]
            )


class TestDescarga:
    async def _documento_listo(
        self, session: AsyncSession, ctx: dict, bucket: str, *, scan: str
    ) -> uuid.UUID:
        subida = await service.preparar_subida(
            session,
            shipment_id=ctx["shipment"],
            document_type_id=ctx["tipo_factura"],
            original_name="factura.pdf",
            company_id=ctx["empresa"],
            actor_user_id=ctx["usuario"],
        )
        s3._cliente().put_object(Bucket=bucket, Key=subida.storage_key, Body=pdf_real())
        await service.completar(session, document_id=subida.document_id, company_id=ctx["empresa"])
        await session.execute(
            text("""
                UPDATE documents SET scan_status = :s, upload_status = 'READY'
                WHERE id = :id
            """),
            {"s": scan, "id": subida.document_id},
        )
        return subida.document_id

    async def test_un_documento_pendiente_de_escaneo_no_se_descarga(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        """Caso 7 del gate. Fail closed: si el escáner no terminó, espera."""
        ctx = await _entorno(session)
        doc = await self._documento_listo(session, ctx, storage_de_prueba, scan=ScanStatus.PENDING)

        with pytest.raises(service.DocumentoNoDisponible):
            await service.preparar_descarga(session, document_id=doc, company_ids=[ctx["empresa"]])

    async def test_un_documento_infectado_no_se_descarga(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        doc = await self._documento_listo(session, ctx, storage_de_prueba, scan=ScanStatus.INFECTED)

        with pytest.raises(service.DocumentoEnCuarentena):
            await service.preparar_descarga(session, document_id=doc, company_ids=[ctx["empresa"]])

    async def test_un_documento_de_otra_empresa_da_404(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        """Caso 6 del gate. Ajeno e inexistente son indistinguibles."""
        from app.core.errors import RecursoNoEncontrado

        ctx = await _entorno(session)
        doc = await self._documento_listo(session, ctx, storage_de_prueba, scan=ScanStatus.CLEAN)

        with pytest.raises(RecursoNoEncontrado):
            await service.preparar_descarga(
                session, document_id=doc, company_ids=[ctx["otra_empresa"]]
            )

    async def test_un_documento_limpio_y_propio_se_descarga(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        doc = await self._documento_listo(session, ctx, storage_de_prueba, scan=ScanStatus.CLEAN)

        descarga = await service.preparar_descarga(
            session, document_id=doc, company_ids=[ctx["empresa"]]
        )

        assert descarga.url.startswith("http")
        # La URL es firmada y temporal, no una ruta pública del bucket.
        assert "X-Amz-Signature" in descarga.url
        assert "X-Amz-Expires" in descarga.url

    async def test_operaciones_descarga_de_cualquier_empresa(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        ctx = await _entorno(session)
        doc = await self._documento_listo(session, ctx, storage_de_prueba, scan=ScanStatus.CLEAN)

        # `None` = alcance global.
        descarga = await service.preparar_descarga(session, document_id=doc, company_ids=None)

        assert descarga.url.startswith("http")


class TestBucketPrivado:
    async def test_el_objeto_no_es_accesible_sin_firma(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        """Caso 8 del gate.

        Sin la firma, conocer la `storage_key` no alcanza: el bucket no tiene
        lectura pública.
        """
        import httpx

        from app.core.config import get_settings

        ctx = await _entorno(session)
        subida = await service.preparar_subida(
            session,
            shipment_id=ctx["shipment"],
            document_type_id=ctx["tipo_factura"],
            original_name="factura.pdf",
            company_id=ctx["empresa"],
            actor_user_id=ctx["usuario"],
        )
        s3._cliente().put_object(Bucket=storage_de_prueba, Key=subida.storage_key, Body=pdf_real())

        url_sin_firmar = (
            f"{get_settings().s3_endpoint_url}/{storage_de_prueba}/{subida.storage_key}"
        )
        async with httpx.AsyncClient() as cliente:
            respuesta = await cliente.get(url_sin_firmar)

        assert respuesta.status_code in (401, 403)

    async def test_la_url_firmada_si_funciona(
        self, session: AsyncSession, storage_de_prueba: str
    ) -> None:
        """La contraparte: con firma válida, la descarga funciona."""
        import httpx

        ctx = await _entorno(session)
        subida = await service.preparar_subida(
            session,
            shipment_id=ctx["shipment"],
            document_type_id=ctx["tipo_factura"],
            original_name="factura.pdf",
            company_id=ctx["empresa"],
            actor_user_id=ctx["usuario"],
        )
        contenido = pdf_real()
        s3._cliente().put_object(Bucket=storage_de_prueba, Key=subida.storage_key, Body=contenido)
        await service.completar(session, document_id=subida.document_id, company_id=ctx["empresa"])
        await session.execute(
            text("""
                UPDATE documents SET scan_status = 'CLEAN', upload_status = 'READY'
                WHERE id = :id
            """),
            {"id": subida.document_id},
        )

        descarga = await service.preparar_descarga(
            session, document_id=subida.document_id, company_ids=[ctx["empresa"]]
        )
        async with httpx.AsyncClient() as cliente:
            respuesta = await cliente.get(descarga.url)

        assert respuesta.status_code == 200
        assert respuesta.content == contenido
