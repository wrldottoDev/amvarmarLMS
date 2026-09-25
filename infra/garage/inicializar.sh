#!/usr/bin/env bash
#
# Deja listo un Garage recién creado: layout del nodo, las claves de acceso y
# el bucket privado. Idempotente: se puede volver a correr sin romper nada.
#
#   cd infra/produccion && ../garage/inicializar.sh
#   cd infra/docker && ENV_FILE=../../backend/.env ../garage/inicializar.sh
#
# Lee del .env (ENV_FILE, por defecto ./.env):
#   S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY          clave de la aplicación (lectura y escritura);
#                                                    durante la migración desde MinIO se usan
#                                                    GARAGE_S3_ACCESS_KEY/GARAGE_S3_SECRET_KEY
#   RESPALDO_S3_ACCESS_KEY, RESPALDO_S3_SECRET_KEY   clave del respaldo (solo lectura), opcional
#   GARAGE_CAPACIDAD                                 capacidad declarada del nodo (por defecto 60G)
#
# Las claves las genera `generar_claves.sh`: Garage exige su formato (GK + 24
# hex el id, 64 hex el secreto) y así el valor no tiene que imprimirse nunca.
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-./.env}"
# Sin `source`: un .env válido para Docker (p. ej. `EMAIL_FROM=Nombre <correo>`)
# no siempre es shell válido.
leer_env() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }

S3_BUCKET="$(leer_env S3_BUCKET)"
: "${S3_BUCKET:?falta S3_BUCKET en ${ENV_FILE}}"
CLAVE_APP="$(leer_env GARAGE_S3_ACCESS_KEY)"
SECRETO_APP="$(leer_env GARAGE_S3_SECRET_KEY)"
if [[ -z "$CLAVE_APP" ]]; then
  CLAVE_APP="$(leer_env S3_ACCESS_KEY)"
  SECRETO_APP="$(leer_env S3_SECRET_KEY)"
fi
[[ "$CLAVE_APP" == GK* && -n "$SECRETO_APP" ]] || {
  echo "ERROR: no hay una clave de Garage (GK...) en ${ENV_FILE}: correr generar_claves.sh" >&2
  exit 1
}
RESPALDO_S3_ACCESS_KEY="$(leer_env RESPALDO_S3_ACCESS_KEY)"
RESPALDO_S3_SECRET_KEY="$(leer_env RESPALDO_S3_SECRET_KEY)"
GARAGE_CAPACIDAD="$(leer_env GARAGE_CAPACIDAD)"
CAPACIDAD="${GARAGE_CAPACIDAD:-60G}"

garage() { docker compose --env-file "$ENV_FILE" exec -T garage /garage "$@"; }

nodo="$(garage node id -q 2>/dev/null | tail -1 | cut -d@ -f1)"
if ! garage layout show 2>/dev/null | grep -q "${nodo:0:16}"; then
  garage layout assign -z vps -c "$CAPACIDAD" "$nodo"
  version="$(garage layout show 2>/dev/null | awk '/apply --version/ {print $NF}' | tail -1)"
  garage layout apply --version "${version:-1}"
fi

importar_clave() {
  local nombre="$1" id="$2" secreto="$3"
  if ! garage key info "$id" >/dev/null 2>&1; then
    garage key import --yes -n "$nombre" "$id" "$secreto" >/dev/null
    echo "Clave ${nombre} importada."
  fi
}

importar_clave amvarmar-lms "$CLAVE_APP" "$SECRETO_APP"
if ! garage bucket info "$S3_BUCKET" >/dev/null 2>&1; then
  garage bucket create "$S3_BUCKET" >/dev/null
  echo "Bucket ${S3_BUCKET} creado."
fi
garage bucket allow --read --write "$S3_BUCKET" --key amvarmar-lms >/dev/null

if [[ -n "${RESPALDO_S3_ACCESS_KEY:-}" ]]; then
  importar_clave amvarmar-respaldo "$RESPALDO_S3_ACCESS_KEY" "$RESPALDO_S3_SECRET_KEY"
  # Solo lectura: un respaldo comprometido no puede borrar ni pisar objetos.
  garage bucket allow --read "$S3_BUCKET" --key amvarmar-respaldo >/dev/null
fi

garage status | tail -3
echo "Garage listo: bucket ${S3_BUCKET}."
