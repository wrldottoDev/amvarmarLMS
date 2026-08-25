"""Validación de archivos subidos (Paso 3.1 + ADR-0009).

Regla central: **nunca se confía en lo que declara el cliente.** Ni la
extensión, ni el `Content-Type` del header, ni el tamaño anunciado. Todo se
verifica contra los bytes reales del archivo ya almacenado.

Un `Content-Type: application/pdf` sobre un ejecutable es trivial de fabricar;
el magic number, no.
"""

import re
import unicodedata
from dataclasses import dataclass

import magic

from app.core.errors import ReglaDeNegocioViolada

# Formato lógico -> MIME que `libmagic` debe reportar. Varios MIME por formato
# porque libmagic distingue variantes que para el negocio son lo mismo.
MIME_POR_FORMATO: dict[str, frozenset[str]] = {
    "PDF": frozenset({"application/pdf"}),
    "JPG": frozenset({"image/jpeg"}),
    "JPEG": frozenset({"image/jpeg"}),
    "PNG": frozenset({"image/png"}),
    "WEBP": frozenset({"image/webp"}),
    "HEIC": frozenset({"image/heic", "image/heif"}),
    "DOCX": frozenset(
        {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            # libmagic a veces solo ve el ZIP contenedor de un Office XML.
            "application/zip",
        }
    ),
    "XLSX": frozenset(
        {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/zip",
        }
    ),
    "CSV": frozenset({"text/csv", "text/plain", "application/csv"}),
    "TXT": frozenset({"text/plain"}),
}

# Extensiones prohibidas siempre, aunque el tipo de documento las listara.
# Bloquear por extensión es más barato y más confiable que intentar detectar
# macros dentro de un Office XML (ADR-0009).
EXTENSIONES_PROHIBIDAS: frozenset[str] = frozenset(
    {
        # Office con macros
        "docm",
        "xlsm",
        "pptm",
        "dotm",
        "xltm",
        # Ejecutables y scripts
        "exe",
        "dll",
        "so",
        "dylib",
        "bat",
        "cmd",
        "com",
        "scr",
        "msi",
        "sh",
        "bash",
        "ps1",
        "vbs",
        "js",
        "jar",
        "app",
        # Comprimidos: prohibidos en subida (ADR-0009), sin excepción
        "zip",
        "rar",
        "7z",
        "tar",
        "gz",
        "bz2",
        "xz",
    }
)

# MIME que se rechazan siempre, sin importar la extensión declarada. Atrapa un
# ejecutable renombrado a `.pdf`.
MIME_PROHIBIDOS: frozenset[str] = frozenset(
    {
        "application/x-dosexec",
        "application/x-executable",
        "application/x-mach-binary",
        "application/x-sharedlib",
        "application/x-msdownload",
        "application/vnd.microsoft.portable-executable",
        "text/x-shellscript",
        "application/x-rar",
        "application/x-7z-compressed",
        "application/gzip",
        "application/x-tar",
    }
)

# libmagic solo necesita la cabecera para identificar el tipo. Leer el archivo
# entero para esto sería desperdiciar memoria en archivos de 250 MB.
BYTES_PARA_DETECTAR = 8192


class ArchivoInvalido(ReglaDeNegocioViolada):
    code = "ARCHIVO_INVALIDO"


class FormatoNoPermitido(ReglaDeNegocioViolada):
    code = "FORMATO_NO_PERMITIDO"


class ArchivoDemasiadoGrande(ReglaDeNegocioViolada):
    code = "ARCHIVO_DEMASIADO_GRANDE"


class ContenidoNoCoincide(ReglaDeNegocioViolada):
    code = "CONTENIDO_NO_COINCIDE_CON_LA_EXTENSION"


@dataclass(frozen=True)
class ResultadoValidacion:
    media_type: str
    formato: str
    safe_name: str


def extension_de(nombre: str) -> str:
    _, _, extension = nombre.rpartition(".")
    return extension.lower() if extension and extension != nombre else ""


def nombre_seguro(nombre: str) -> str:
    """Nombre apto para servir en `Content-Disposition`.

    Un nombre de archivo llega del cliente: puede traer separadores de ruta,
    caracteres de control o saltos de línea que rompan la cabecera HTTP. Se
    normaliza en vez de rechazar, porque el nombre original se conserva aparte
    y no queremos rechazar una factura legítima por llamarse raro.
    """
    # Quitar cualquier componente de ruta: solo interesa el nombre base.
    base = nombre.replace("\\", "/").rpartition("/")[2]
    # Normalizar acentos a ASCII para evitar problemas de codificación.
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    base = re.sub(r"_{2,}", "_", base)

    if not base:
        base = "documento"

    return base[:255]


def validar_extension(nombre: str) -> str:
    extension = extension_de(nombre)

    if not extension:
        raise ArchivoInvalido("El archivo no tiene extensión.")

    if extension in EXTENSIONES_PROHIBIDAS:
        raise FormatoNoPermitido(f"Los archivos .{extension} no se aceptan por seguridad.")

    return extension


def validar_tamano(size_bytes: int, *, maximo: int) -> None:
    if size_bytes <= 0:
        # Un archivo de 0 bytes nunca es un documento válido y suele indicar
        # que la subida se cortó.
        raise ArchivoInvalido("El archivo está vacío.")

    if size_bytes > maximo:
        megas = maximo / (1024 * 1024)
        raise ArchivoDemasiadoGrande(f"El archivo supera el máximo permitido de {megas:.0f} MB.")


def detectar_media_type(cabecera: bytes) -> str:
    """MIME real, leído de los bytes. Nunca del header del cliente."""
    return str(magic.from_buffer(cabecera, mime=True))


def validar_contenido(
    *,
    cabecera: bytes,
    nombre: str,
    formatos_permitidos: list[str],
) -> ResultadoValidacion:
    """Verifica que el contenido real coincida con lo declarado.

    Atrapa los dos ataques básicos: un PDF renombrado a `.jpg` (extensión que
    miente) y un ejecutable con `Content-Type: application/pdf` (header que
    miente).
    """
    extension = validar_extension(nombre)
    media_type = detectar_media_type(cabecera)

    if media_type in MIME_PROHIBIDOS:
        raise FormatoNoPermitido("El contenido del archivo no se acepta por seguridad.")

    permitidos = {f.upper() for f in formatos_permitidos}

    formato_por_extension = extension.upper()
    if formato_por_extension not in permitidos:
        raise FormatoNoPermitido(
            f"Este tipo de documento no acepta archivos .{extension}. "
            f"Formatos aceptados: {', '.join(sorted(permitidos))}."
        )

    mimes_esperados = MIME_POR_FORMATO.get(formato_por_extension, frozenset())
    if media_type not in mimes_esperados:
        # La extensión dice una cosa y los bytes dicen otra. No se acepta
        # aunque el contenido real fuera un formato permitido: un archivo mal
        # etiquetado confunde a quien lo abra después.
        raise ContenidoNoCoincide(
            f"El archivo dice ser .{extension} pero su contenido es {media_type}."
        )

    return ResultadoValidacion(
        media_type=media_type,
        formato=formato_por_extension,
        safe_name=nombre_seguro(nombre),
    )
