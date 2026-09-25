#!/usr/bin/env bash
#
# Copia el bucket de MinIO a Garage y verifica la copia byte a byte.
# Solo lee de MinIO: no borra ni cambia nada ahí.
#
#   cd infra/produccion && ../garage/copiar_desde_minio.sh            # copia (se puede repetir: solo trae lo nuevo)
#   cd infra/produccion && ../garage/copiar_desde_minio.sh verificar  # compara el contenido de cada objeto
#
# rclone corre en un contenedor dentro de la red del compose: le habla a
# `minio:9000` y `garage:3900` directo, sin nginx.
#
# Variables: ENV_FILE (por defecto ./.env), RED (por defecto <proyecto>_default).
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-./.env}"
RCLONE_IMAGEN="${RCLONE_IMAGEN:-rclone/rclone:1.75.1}"
leer_env() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }

proyecto="$(docker compose --env-file "$ENV_FILE" config --format json 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
RED="${RED:-${proyecto}_default}"

bucket="$(leer_env S3_BUCKET)"
minio_usuario="$(leer_env MINIO_ROOT_USER)"
minio_usuario="${minio_usuario:-amvarmar}"
minio_clave="$(leer_env MINIO_ROOT_PASSWORD)"
garage_clave="$(leer_env GARAGE_S3_ACCESS_KEY)"
garage_secreto="$(leer_env GARAGE_S3_SECRET_KEY)"
if [[ -z "$garage_clave" ]]; then
  garage_clave="$(leer_env S3_ACCESS_KEY)"
  garage_secreto="$(leer_env S3_SECRET_KEY)"
fi
[[ -n "$bucket" && -n "$minio_clave" && "$garage_clave" == GK* ]] || {
  echo "ERROR: faltan S3_BUCKET, MINIO_ROOT_PASSWORD o la clave de Garage en ${ENV_FILE}" >&2
  exit 1
}

rclone() {
  docker run --rm --network "$RED" \
    -e RCLONE_CONFIG_MINIO_TYPE=s3 \
    -e RCLONE_CONFIG_MINIO_PROVIDER=Minio \
    -e RCLONE_CONFIG_MINIO_ENDPOINT=http://minio:9000 \
    -e RCLONE_CONFIG_MINIO_ACCESS_KEY_ID="$minio_usuario" \
    -e RCLONE_CONFIG_MINIO_SECRET_ACCESS_KEY="$minio_clave" \
    -e RCLONE_CONFIG_GARAGE_TYPE=s3 \
    -e RCLONE_CONFIG_GARAGE_PROVIDER=Other \
    -e RCLONE_CONFIG_GARAGE_ENDPOINT=http://garage:3900 \
    -e RCLONE_CONFIG_GARAGE_REGION=us-east-1 \
    -e RCLONE_CONFIG_GARAGE_FORCE_PATH_STYLE=true \
    -e RCLONE_CONFIG_GARAGE_ACCESS_KEY_ID="$garage_clave" \
    -e RCLONE_CONFIG_GARAGE_SECRET_ACCESS_KEY="$garage_secreto" \
    "$RCLONE_IMAGEN" "$@"
}

case "${1:-copiar}" in
  copiar)
    # `copy` y no `sync`: nunca borra en Garage lo que no esté en MinIO.
    # Conserva Content-Type y Content-Encoding (los archivados de ADR-0007
    # dependen de este último para descargarse bien).
    rclone copy "minio:${bucket}" "garage:${bucket}" \
      --transfers 8 --checkers 16 --metadata --stats-one-line --stats 30s -v 2>&1 | tail -8
    ;;
  verificar)
    # `--download`: baja los dos lados y compara el contenido. El ETag de un
    # objeto multipart no es un MD5, así que comparar hashes no alcanzaría.
    rclone check "minio:${bucket}" "garage:${bucket}" --download --one-way --checkers 8 2>&1 | tail -4
    echo "Objetos en MinIO:  $(rclone size "minio:${bucket}" --json)"
    echo "Objetos en Garage: $(rclone size "garage:${bucket}" --json)"
    ;;
  *)
    echo "uso: $0 [copiar|verificar]" >&2
    exit 2
    ;;
esac
