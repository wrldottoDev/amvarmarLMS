"""Storage privado S3-compatible (MinIO en local, S3 o equivalente en producción).

El bucket NO tiene lectura pública. Todo acceso pasa por una URL firmada de
corta duración que el backend emite tras verificar permisos: sin eso, conocer
la `storage_key` bastaría para descargar el documento de cualquier empresa.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings

# Ventanas cortas a propósito. Una URL firmada es un token de acceso: cuanto
# más vive, más tiempo sirve si se filtra por el historial del navegador, un
# reenvío de correo o un log de proxy.
TTL_SUBIDA_SEGUNDOS = 300  # 5 minutos
TTL_DESCARGA_SEGUNDOS = 120  # 2 minutos

# Umbral de multipart: por encima, la subida se parte (ADR-0009). 5 MB es el
# mínimo de parte que admite S3.
UMBRAL_MULTIPART_BYTES = 5 * 1024 * 1024


class ObjetoNoEncontrado(Exception):
    """La `storage_key` no existe en el bucket."""


@dataclass(frozen=True)
class ObjetoAlmacenado:
    size_bytes: int
    etag: str


@lru_cache
def _cliente() -> Any:
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        # `s3v4` es obligatorio para que MinIO acepte las URLs firmadas.
        config=Config(signature_version="s3v4"),
    )


def _bucket() -> str:
    return get_settings().s3_bucket


async def _en_hilo(funcion: Any, *args: Any, **kwargs: Any) -> Any:
    """boto3 es síncrono; sin esto bloquearía el event loop de FastAPI."""
    return await asyncio.to_thread(funcion, *args, **kwargs)


async def url_de_subida(storage_key: str) -> str:
    """URL firmada para que el cliente suba directo al storage.

    El archivo no pasa por la aplicación: subir 250 MB a través de FastAPI
    ocuparía un worker todo ese tiempo y forzaría a bufferear en memoria.

    La firma cubre SOLO bucket y clave, deliberadamente. Incluir `ContentType`
    obligaría al cliente a mandar ese header exacto o la firma no valida —
    frágil, y además inútil: el `Content-Type` que declare el cliente no se usa
    para nada. El tipo real se detecta de los bytes en `completar()`.

    El cifrado en reposo tampoco va en la firma: está configurado como default
    del bucket, así que aplica aunque el cliente no mande el header. Si
    dependiera del cliente, omitirlo lo desactivaría.
    """
    return str(
        await _en_hilo(
            _cliente().generate_presigned_url,
            "put_object",
            Params={"Bucket": _bucket(), "Key": storage_key},
            ExpiresIn=TTL_SUBIDA_SEGUNDOS,
        )
    )


async def url_de_descarga(storage_key: str, *, nombre_archivo: str) -> str:
    """URL firmada de descarga, con el nombre original para el usuario."""
    # Comillas escapadas: un nombre con `"` rompería la cabecera.
    seguro = nombre_archivo.replace('"', "")
    return str(
        await _en_hilo(
            _cliente().generate_presigned_url,
            "get_object",
            Params={
                "Bucket": _bucket(),
                "Key": storage_key,
                "ResponseContentDisposition": f'attachment; filename="{seguro}"',
            },
            ExpiresIn=TTL_DESCARGA_SEGUNDOS,
        )
    )


async def describir_objeto(storage_key: str) -> ObjetoAlmacenado:
    """Metadata del objeto. Lanza `ObjetoNoEncontrado` si no existe.

    Se consulta en `complete` para confirmar que el cliente realmente subió
    algo: sin esto, bastaría llamar a `complete` sin subir nada para dejar un
    documento fantasma marcado como válido.
    """
    try:
        respuesta = await _en_hilo(_cliente().head_object, Bucket=_bucket(), Key=storage_key)
    except ClientError as error:
        codigo = error.response.get("Error", {}).get("Code")
        if codigo in {"404", "NoSuchKey", "NotFound"}:
            raise ObjetoNoEncontrado(storage_key) from error
        raise

    return ObjetoAlmacenado(
        size_bytes=int(respuesta["ContentLength"]),
        etag=str(respuesta.get("ETag", "")).strip('"'),
    )


async def leer_rango(storage_key: str, *, bytes_iniciales: int) -> bytes:
    """Lee solo la cabecera del objeto, para detectar el MIME real.

    Descargar 250 MB para mirar los primeros 8 KB sería absurdo.
    """
    respuesta = await _en_hilo(
        _cliente().get_object,
        Bucket=_bucket(),
        Key=storage_key,
        Range=f"bytes=0-{bytes_iniciales - 1}",
    )
    cuerpo = respuesta["Body"]
    try:
        return bytes(await _en_hilo(cuerpo.read))
    finally:
        await _en_hilo(cuerpo.close)


async def iterar_chunks(
    storage_key: str, *, chunk_bytes: int = 8 * 1024 * 1024
) -> AsyncIterator[bytes]:
    """Lee un objeto con memoria acotada y cierra siempre el StreamingBody."""
    respuesta = await _en_hilo(_cliente().get_object, Bucket=_bucket(), Key=storage_key)
    cuerpo = respuesta["Body"]
    try:
        while True:
            chunk = await _en_hilo(cuerpo.read, chunk_bytes)
            if not chunk:
                break
            yield bytes(chunk)
    finally:
        await _en_hilo(cuerpo.close)


async def eliminar(storage_key: str) -> None:
    """Borra un objeto huérfano.

    Solo para limpiar subidas que nunca se completaron. Los documentos ya
    válidos NO se eliminan: ADR-0007 los recomprime y archiva.
    """
    await _en_hilo(_cliente().delete_object, Bucket=_bucket(), Key=storage_key)


async def subir_archivo(
    ruta_local: str, storage_key: str, *, media_type: str, content_encoding: str | None = None
) -> None:
    """Sube desde disco con multipart; nunca materializa el archivo en RAM.

    `content_encoding`: solo lo usa el archivado de ADR-0007, para un PDF
    firmado cuyo contenido se comprimió en gzip sin tocar un byte del PDF en
    sí — el cliente HTTP lo descomprime solo al descargar (`Content-Encoding`
    es un mecanismo HTTP estándar), así que el endpoint de descarga no
    necesita ningún cambio.
    """
    transferencia = TransferConfig(
        multipart_threshold=UMBRAL_MULTIPART_BYTES,
        multipart_chunksize=8 * 1024 * 1024,
        max_concurrency=2,
        use_threads=True,
    )
    extra: dict[str, str] = {"ContentType": media_type}
    if content_encoding:
        extra["ContentEncoding"] = content_encoding
    await _en_hilo(
        _cliente().upload_file,
        ruta_local,
        _bucket(),
        storage_key,
        ExtraArgs=extra,
        Config=transferencia,
    )


# Códigos con los que un proveedor S3-compatible dice "no implemento esto".
_NO_SOPORTADO = frozenset({"MalformedXML", "NotImplemented", "MethodNotAllowed", "InvalidRequest"})


def _intentar_extension_s3(operacion: Any, **kwargs: Any) -> None:
    """Aplica una configuración que no todo proveedor S3-compatible soporta."""
    try:
        operacion(**kwargs)
    except ClientError as error:
        codigo = error.response.get("Error", {}).get("Code")
        if codigo not in _NO_SOPORTADO:
            # Un fallo distinto (permisos, bucket inexistente) sí importa.
            raise


def asegurar_bucket_privado() -> None:
    """Crea el bucket si falta, lo deja privado y con cifrado por defecto.

    Síncrona a propósito: corre al arrancar la aplicación y en los tests, no
    dentro de un request. Hacerla async obligaría a los llamadores a manejar un
    event loop para algo que ocurre una vez.

    La verificación vale aunque en producción el bucket lo cree la infra: un
    bucket con lectura pública es la falla más común y más silenciosa de este
    tipo de sistemas.
    """
    cliente = _cliente()
    bucket = _bucket()

    try:
        cliente.head_bucket(Bucket=bucket)
    except ClientError:
        cliente.create_bucket(Bucket=bucket)

    # `PutPublicAccessBlock` y `PutBucketEncryption` son extensiones de AWS S3.
    # MinIO no las implementa: sus buckets nacen privados y sin política
    # anónima, que es la misma garantía por otro camino.
    #
    # Se intentan igual y se ignora solo el "no soportado": en S3 real sí
    # aplican, y silenciar un fallo distinto dejaría un bucket público sin que
    # nadie se entere. La verificación que de verdad importa es el test que
    # comprueba que un objeto no se descarga sin firma.
    _intentar_extension_s3(
        cliente.put_public_access_block,
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )

    # Cifrado en reposo por defecto del bucket (ADR-0009). A nivel de bucket y
    # no por petición: así aplica a todo objeto, incluido el que suba un cliente
    # que no mande el header.
    _intentar_extension_s3(
        cliente.put_bucket_encryption,
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )
