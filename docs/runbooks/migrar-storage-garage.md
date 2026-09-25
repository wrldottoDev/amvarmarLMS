# Storage: de MinIO a Garage, y respaldo de archivos

MinIO archivó su código y dejó de publicar imágenes y parches. El reemplazo es
**Garage** (`dxflrs/garage`), que se sigue manteniendo: mismo protocolo S3, así
que el backend no cambia, solo las claves. La suite de storage (101 tests) corre
contra Garage (`tests/conftest.py`).

Se ejecuta en `/opt/amvarmar-lms/infra/produccion`. Cada fase termina con un
reporte y una pausa. MinIO **solo se lee** hasta la fase G6.

## Espacio en disco

La copia duplica los objetos (~11,3 GB) hasta que se borre MinIO, y el respaldo
suma otro tanto. Orden obligatorio: **G6 (borrar MinIO) antes de G7 (primer
respaldo)**. Si no, el disco queda casi lleno.

## G0 — Preparación (sin corte)

```bash
git pull
../garage/generar_claves.sh          # GARAGE_RPC_SECRET, GARAGE_S3_*, RESPALDO_S3_*: sin imprimirlas
docker compose up -d garage          # MinIO sigue arriba, sin cambios
docker compose ps garage             # healthy
../garage/inicializar.sh             # layout, claves y bucket privado
```

`generar_claves.sh` no toca `S3_ACCESS_KEY`/`S3_SECRET_KEY`: la aplicación
sigue usando MinIO hasta G3.

## G1 — Primera copia (con el sistema andando)

```bash
../garage/copiar_desde_minio.sh
```

Copia todo sin cortar el servicio. Se puede repetir: cada vez solo trae lo nuevo.

## G2 — Congelar las escrituras

```bash
docker compose stop backend worker beat
```

Sin backend no se emiten URLs de subida nuevas. Las que ya se emitieron vencen
a los 5 minutos: esperar 5 minutos antes de seguir. El sitio muestra error
hasta G4.

## G3 — Última copia, verificación y claves

```bash
../garage/copiar_desde_minio.sh              # lo que haya entrado desde G1
../garage/copiar_desde_minio.sh verificar    # "0 differences found"; mismos count y bytes
../garage/activar_claves.sh                  # la app pasa a la clave de Garage (respaldo en .env.antes-garage)
```

Si `verificar` muestra diferencias: pausa. La app sigue configurada para MinIO,
así que volver es `docker compose start backend worker beat`.

## G4 — nginx y arranque

```bash
sudo cp /etc/nginx/sites-available/produccion-app.amvarmar.com.conf /opt/respaldos/produccion-app.amvarmar.com.conf.antes-garage
sudo cp nginx/produccion-app.amvarmar.com.conf /etc/nginx/sites-available/
sudo nginx -t && sudo systemctl reload nginx
docker compose up -d --wait --force-recreate backend worker beat
```

El sitio nuevo manda `/amvarmar-documentos/` a Garage (puerto 59010) y no a MinIO (59000).

## G5 — Pruebas

1. `https://app.amvarmar.com/amvarmar-documentos/` da 403 con XML de Garage.
2. Desde el backend: `url_de_subida` → PUT por curl → `describir_objeto` → `eliminar`.
3. Cada documento de la base, releído desde Garage y con su sha256 recalculado
   (incluye los subidos después del cutover): 0 faltantes, 0 discrepancias.
   ```bash
   docker compose run --rm backend python -m scripts.migrate_files --media-root /tmp --verificar
   ```
4. En el navegador: descargar un documento migrado y subir uno nuevo, que pase a disponible.

**Volver atrás (antes de G6):** restaurar `.env.antes-garage` y el sitio de
nginx desde `/opt/respaldos`, `nginx -t`, reload y recrear backend, worker y
beat. MinIO no se tocó. Lo que se haya subido después de G4 queda solo en
Garage: copiarlo de vuelta con rclone antes de volver.

## G6 — Borrar MinIO (con confirmación de Otto)

Recién después de G5 en verde:

```bash
docker stop amvarmar-lms-prod-minio-1 && docker rm amvarmar-lms-prod-minio-1
docker volume rm amvarmar-lms-prod_minio-data
docker image rm amvarmar-minio:RELEASE.2025-10-15T17-29-55Z
```

Libera ~11 GB. Después se quitan del repo el servicio `minio` del compose e `infra/minio`.

## G7 — Respaldo diario de archivos

```bash
COMPOSE_DIR=/opt/amvarmar-lms/infra/produccion BACKUP_DIR=/opt/respaldos/archivos \
  /opt/amvarmar-lms/infra/backup/respaldar_archivos.sh      # primera corrida: copia todo
crontab -e
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

Después de restaurar: `migrate_files --verificar` (G5.3).
