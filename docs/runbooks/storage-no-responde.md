# El storage no responde

**Alerta:** `StorageNoResponde`

## Qué significa

MinIO/S3 no devuelve los objetos. Los efectos se ven en dos lados: las subidas
quedan a medias —el documento existe en la base pero el objeto no— y las
descargas fallan aunque el documento figure como disponible.

El antivirus ya no participa: se retiró por decisión de AMVARMAR (ver la
enmienda de ADR-0009), así que un documento es descargable en cuanto termina de
subirse.

## Diagnóstico

```bash
# ¿El storage responde?
curl -fsS "$S3_ENDPOINT_URL/minio/health/live"

# ¿El worker está vivo?
celery -A app.workers.app.celery_app inspect active
```

```sql
-- Subidas que quedaron a medias, y desde cuándo
SELECT upload_status, count(*), min(created_at) AS mas_viejo
FROM documents WHERE deleted_at IS NULL GROUP BY 1;
```

Una cifra alta y creciente en `UPLOADING` es la señal: el cliente pidió la URL
firmada, subió el archivo y el paso de cierre no pudo leer los bytes.

## Qué hacer

**Storage caído** — levantarlo. No hace falta reencolar nada: los documentos en
`UPLOADING` se cierran cuando el cliente reintenta la subida.

**Storage vivo pero las subidas siguen fallando** — mirar los logs del backend
buscando `documento_ilegible`. Un objeto concreto que falla siempre puede estar
corrupto en el bucket; el registro tiene su `storage_key`.

**Documentos viejos en `UPLOADING`** — son subidas que el cliente abandonó a
mitad de camino. Se pueden pedir de nuevo desde el expediente; no se arreglan
solas.

## Qué NO hacer

- **Nunca marcar documentos como `READY` a mano para vaciar la cola.** Un
  documento `READY` cuyo objeto no está completo se descarga truncado, que es
  peor que no descargarse: parece un archivo válido y no lo es.
- **No borrar los documentos pendientes.** Son archivos que un cliente subió y
  espera encontrar.
