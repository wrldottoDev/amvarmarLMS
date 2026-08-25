# El storage no responde / escaneos atascados

**Alerta:** `EscaneoAtascado`

## Qué significa

Hay documentos subidos esperando el antivirus. Como el sistema es **fail
closed**, un documento sin escanear no se puede descargar: además de la cola,
hay clientes que no pueden ver sus archivos.

Dos causas posibles y se distinguen rápido: o ClamAV está caído, o el storage no
devuelve los bytes que hay que escanear.

## Diagnóstico

```bash
# ¿ClamAV responde? Debe contestar PONG.
echo "PING" | nc "$CLAMAV_HOST" 3310

# ¿El storage responde?
curl -fsS "$S3_ENDPOINT_URL/minio/health/live"

# ¿El worker está vivo?
celery -A app.workers.app.celery_app inspect active
```

```sql
-- Cuántos y desde cuándo
SELECT scan_status, count(*), min(created_at) AS mas_viejo
FROM documents WHERE deleted_at IS NULL GROUP BY 1;
```

## Qué hacer

**ClamAV caído** — levantarlo y esperar. Cargar la base de firmas toma cerca de
un minuto; el health check (`clamdcheck.sh`) comprueba que ya cargó, no solo que
el proceso exista. Los documentos pendientes se escanean solos en la siguiente
pasada, sin reencolar nada a mano.

**Storage caído** — resolverlo primero. El worker deja los documentos pendientes
y reintenta; no marca nada como limpio.

**Los dos vivos pero la cola no baja** — mirar los logs del worker buscando
`escaner_no_disponible` y `documento_ilegible`. Un documento concreto que falla
siempre puede estar corrupto en el bucket.

## Qué NO hacer

- **Nunca marcar documentos como `CLEAN` a mano para vaciar la cola.** Es
  exactamente lo que el diseño fail closed impide. Un archivo no verificado que
  se marca limpio queda descargable para todos los clientes de esa empresa.
- **No borrar los documentos pendientes.** Son archivos que un cliente subió y
  espera encontrar.
