#!/usr/bin/env bash
#
# Respaldo diario de los archivos (los objetos del bucket de Garage).
#
# Complementa a respaldar.sh (la base): sin los objetos, un respaldo de la base
# solo tiene nombres de documentos que ya no se pueden descargar.
#
#   - `actual/` es un espejo del bucket al momento del respaldo.
#   - Lo que se borró o cambió en el bucket desde el respaldo anterior NO se
#     pierde: rclone lo mueve a `cambios/<fecha>/` (--backup-dir). Así un
#     borrado por error, o un bucket vaciado, se recupera desde ahí.
#   - `cambios/` se purga pasados RETENCION_DIAS.
#
# Lee el bucket con una clave de SOLO LECTURA (RESPALDO_S3_*): si alguien se
# hace con ella, no puede borrar ni pisar documentos.
#
# rclone corre en un contenedor dentro de la red del compose y le habla a
# Garage directo, sin pasar por nginx ni Cloudflare.
#
#   COMPOSE_DIR=/opt/amvarmar-lms/infra/produccion BACKUP_DIR=/opt/respaldos/archivos \
#     /opt/amvarmar-lms/infra/backup/respaldar_archivos.sh
#
# Restaurar (todo el bucket, o una carpeta): ver docs/runbooks/migrar-storage-garage.md.
set -Eeuo pipefail

: "${COMPOSE_DIR:?falta COMPOSE_DIR (la carpeta con el docker-compose.yml y el .env)}"
: "${BACKUP_DIR:?falta BACKUP_DIR}"
RETENCION_DIAS="${RETENCION_DIAS:-90}"
RCLONE_IMAGEN="${RCLONE_IMAGEN:-rclone/rclone:1.75.1}"
RED="${RED:-amvarmar-lms-prod_default}"

ENV_FILE="${ENV_FILE:-${COMPOSE_DIR}/.env}"
leer_env() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }

bucket="$(leer_env S3_BUCKET)"
clave="$(leer_env RESPALDO_S3_ACCESS_KEY)"
secreto="$(leer_env RESPALDO_S3_SECRET_KEY)"
[[ -n "$bucket" && -n "$clave" && -n "$secreto" ]] || {
  echo "ERROR: faltan S3_BUCKET o RESPALDO_S3_* en ${ENV_FILE}" >&2
  exit 1
}

marca="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "${BACKUP_DIR}/actual" "${BACKUP_DIR}/cambios"

rclone() {
  # Las credenciales viajan por variables de entorno del contenedor, no por la
  # línea de comandos: así no quedan en `ps` ni en el historial.
  docker run --rm --network "$RED" \
    --user "$(id -u):$(id -g)" \
    -v "${BACKUP_DIR}:/respaldo" \
    -e RCLONE_CONFIG_GARAGE_TYPE=s3 \
    -e RCLONE_CONFIG_GARAGE_PROVIDER=Other \
    -e RCLONE_CONFIG_GARAGE_ENDPOINT=http://garage:3900 \
    -e RCLONE_CONFIG_GARAGE_REGION=us-east-1 \
    -e RCLONE_CONFIG_GARAGE_FORCE_PATH_STYLE=true \
    -e RCLONE_CONFIG_GARAGE_ACCESS_KEY_ID="$clave" \
    -e RCLONE_CONFIG_GARAGE_SECRET_ACCESS_KEY="$secreto" \
    "$RCLONE_IMAGEN" "$@"
}

echo "== ${marca}: respaldo de ${bucket}"
rclone sync "garage:${bucket}" /respaldo/actual \
  --backup-dir "/respaldo/cambios/${marca}" \
  --transfers 4 --checkers 8 --stats-one-line --stats 0 -v 2>&1 | tail -5

# Verificación: cada objeto del bucket está en el espejo, con el mismo tamaño.
# `--one-way`: lo que sobre en el espejo no es un error (lo acaba de mover
# sync, o es de un objeto que se borró durante la corrida).
rclone check "garage:${bucket}" /respaldo/actual --one-way --size-only 2>&1 | tail -2

# Retención: solo la carpeta de cambios. El espejo nunca se purga.
find "${BACKUP_DIR}/cambios" -mindepth 1 -maxdepth 1 -type d -mtime "+${RETENCION_DIAS}" \
  -exec rm -rf {} +
find "${BACKUP_DIR}/cambios" -mindepth 1 -maxdepth 1 -type d -empty -delete

objetos="$(find "${BACKUP_DIR}/actual" -type f | wc -l)"
tamano="$(du -sh "${BACKUP_DIR}/actual" | cut -f1)"
echo "== OK: ${objetos} objetos, ${tamano} en ${BACKUP_DIR}/actual"
