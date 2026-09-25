# Storage: Garage y respaldo de archivos

Los documentos viven en **Garage** (`dxflrs/garage`, servicio `garage` del
compose), un storage S3 que se sigue manteniendo. El backend le habla S3
estándar; nginx publica el bucket en `https://app.amvarmar.com/amvarmar-documentos/`
(puerto local `PUERTO_STORAGE`, 59010) y el navegador sube y descarga ahí con
URLs firmadas.

**Historia:** hasta el 2026-09-25 el storage era MinIO, que archivó su código y
dejó de publicar parches. La migración (copia con rclone, verificación byte a
byte, 6 min de corte, 2240 documentos verificados por sha256) está en el commit
93a7b59, con los scripts que se usaron y ya se borraron.

## Instalación nueva

En `infra/produccion` (o `infra/docker` con `ENV_FILE=../../backend/.env`):

```bash
../garage/generar_claves.sh        # GARAGE_RPC_SECRET, S3_ACCESS_KEY/SECRET y RESPALDO_S3_*: sin imprimirlas
docker compose up -d garage
../garage/inicializar.sh           # layout del nodo, claves y bucket privado (idempotente)
```

## Estado y diagnóstico

```bash
docker compose ps garage
docker compose exec garage /garage status
docker compose exec garage /garage bucket info amvarmar-documentos   # objetos y tamaño
curl -s https://app.amvarmar.com/amvarmar-documentos/                # 403 con XML de Garage
docker compose run --rm backend python -m scripts.migrate_files --media-root /tmp --verificar
```

La última relee cada documento `READY` desde Garage y recalcula su sha256.

## Respaldo diario de archivos

```bash
crontab -l
# 15 4 * * * COMPOSE_DIR=/opt/amvarmar-lms/infra/produccion BACKUP_DIR=/opt/respaldos/archivos /opt/amvarmar-lms/infra/backup/respaldar_archivos.sh >> /home/ubuntu/respaldo-archivos.log 2>&1
```

A las 04:15, después del respaldo de la base (03:30). Deja:

- `/opt/respaldos/archivos/actual/`: espejo del bucket.
- `/opt/respaldos/archivos/cambios/<fecha>/`: lo que se borró o cambió ese día.
  Se guarda 90 días.

Usa una clave de **solo lectura**: no puede borrar ni pisar objetos del bucket.
Queda en la misma VPS (decisión de Otto): protege de un borrado por error, no de
perder la VPS. Para eso, copiar `/opt/respaldos` afuera como en el cutover (C7).

## Restaurar

Todo el bucket, desde el espejo (con la clave de la app, que sí escribe):

```bash
docker run --rm --network amvarmar-lms-prod_default -v /opt/respaldos/archivos:/respaldo:ro \
  -e RCLONE_CONFIG_G_TYPE=s3 -e RCLONE_CONFIG_G_PROVIDER=Other \
  -e RCLONE_CONFIG_G_ENDPOINT=http://garage:3900 -e RCLONE_CONFIG_G_REGION=us-east-1 \
  -e RCLONE_CONFIG_G_FORCE_PATH_STYLE=true \
  -e RCLONE_CONFIG_G_ACCESS_KEY_ID="$(grep ^S3_ACCESS_KEY= .env | cut -d= -f2-)" \
  -e RCLONE_CONFIG_G_SECRET_ACCESS_KEY="$(grep ^S3_SECRET_KEY= .env | cut -d= -f2-)" \
  rclone/rclone:1.75.1 copy /respaldo/actual g:amvarmar-documentos
```

Un archivo borrado por error: el mismo comando, con origen
`/respaldo/cambios/<fecha>/<storage_key>` y destino
`g:amvarmar-documentos/<carpeta de la storage_key>`. La `storage_key` sale de la
tabla `documents`.

Después de restaurar: `migrate_files --verificar` (ver "Estado y diagnóstico").

## Espacio en disco y alertas

El espejo del respaldo ocupa lo mismo que el bucket y crece con él: con el
respaldo en la misma VPS, el disco guarda cada documento dos veces.

`infra/backup/vigilar.sh` corre cada hora y manda un correo a `ALERTA_CORREO`
(del `.env`) si el disco pasa del 90 %, si el respaldo de la base o el de
archivos no corrió bien en las últimas 26 h, o si hay avisos del outbox sin
procesar hace más de 15 minutos (beat o worker caídos). Cada problema avisa como mucho una
vez por día. Log: `/home/ubuntu/vigilar.log`.

```bash
# 0 * * * * COMPOSE_DIR=/opt/amvarmar-lms/infra/produccion /opt/amvarmar-lms/infra/backup/vigilar.sh >> /home/ubuntu/vigilar.log 2>&1
```
