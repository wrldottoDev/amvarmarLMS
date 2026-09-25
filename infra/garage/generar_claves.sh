#!/usr/bin/env bash
#
# Escribe en el .env (ENV_FILE, por defecto ./.env) las claves que Garage
# necesita, SIN imprimirlas: GARAGE_RPC_SECRET, S3_ACCESS_KEY/S3_SECRET_KEY
# (la aplicación) y RESPALDO_S3_* (el respaldo, solo lectura). No pisa una
# variable que ya tenga valor.
#
#   cd infra/produccion && ../garage/generar_claves.sh
#   ENV_FILE=backend/.env infra/garage/generar_claves.sh
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-./.env}"
[[ -f "$ENV_FILE" ]] || { echo "No existe ${ENV_FILE}" >&2; exit 1; }

hex() { python3 -c "import secrets,sys; print(secrets.token_hex(int(sys.argv[1])))" "$1"; }

valor() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }

poner() {
  local nombre="$1" nuevo="$2"
  if grep -qE "^${nombre}=" "$ENV_FILE"; then
    [[ -n "$(valor "$nombre")" ]] && return 0
    python3 - "$ENV_FILE" "$nombre" "$nuevo" <<'PY'
import sys, re, pathlib
archivo, nombre, nuevo = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(archivo)
p.write_text(re.sub(rf"^{nombre}=.*$", f"{nombre}={nuevo}", p.read_text(), count=1, flags=re.M))
PY
  else
    # Si el archivo no termina en salto de línea, la variable quedaría
    # pegada a la última.
    [[ -z "$(tail -c1 "$ENV_FILE")" ]] || echo >> "$ENV_FILE"
    printf '%s=%s\n' "$nombre" "$nuevo" >> "$ENV_FILE"
  fi
  echo "${nombre}: generada."
}

poner GARAGE_RPC_SECRET "$(hex 32)"

actual="$(valor S3_ACCESS_KEY)"
if [[ -n "$actual" && "$actual" != GK* ]]; then
  echo "S3_ACCESS_KEY tiene una clave que no es de Garage: vaciarla (y S3_SECRET_KEY) y volver a correr." >&2
  exit 1
fi
poner S3_ACCESS_KEY "GK$(hex 12)"
poner S3_SECRET_KEY "$(hex 32)"
poner RESPALDO_S3_ACCESS_KEY "GK$(hex 12)"
poner RESPALDO_S3_SECRET_KEY "$(hex 32)"
chmod 600 "$ENV_FILE"
