#!/usr/bin/env bash
#
# Corte a Garage: la aplicación pasa a usar la clave de Garage.
# S3_ACCESS_KEY/S3_SECRET_KEY toman los valores de GARAGE_S3_ACCESS_KEY/
# GARAGE_S3_SECRET_KEY, y esas dos se vacían. Guarda antes una copia del .env
# (<.env>.antes-garage, permisos 600). No imprime ningún valor.
#
#   cd infra/produccion && ../garage/activar_claves.sh
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-./.env}"
[[ -f "$ENV_FILE" ]] || { echo "No existe ${ENV_FILE}" >&2; exit 1; }

install -m 600 "$ENV_FILE" "${ENV_FILE}.antes-garage"
python3 - "$ENV_FILE" <<'PY'
import pathlib, re, sys

p = pathlib.Path(sys.argv[1])
texto = p.read_text()

def valor(nombre: str) -> str:
    m = re.findall(rf"^{nombre}=(.*)$", texto, flags=re.M)
    return m[-1].strip() if m else ""

clave, secreto = valor("GARAGE_S3_ACCESS_KEY"), valor("GARAGE_S3_SECRET_KEY")
if not clave.startswith("GK") or not secreto:
    sys.exit("No hay GARAGE_S3_ACCESS_KEY/GARAGE_S3_SECRET_KEY para activar.")
for nombre, nuevo in (
    ("S3_ACCESS_KEY", clave),
    ("S3_SECRET_KEY", secreto),
    ("GARAGE_S3_ACCESS_KEY", ""),
    ("GARAGE_S3_SECRET_KEY", ""),
):
    texto = re.sub(rf"^{nombre}=.*$", f"{nombre}={nuevo}", texto, flags=re.M)
p.write_text(texto)
print("Claves de Garage activas en S3_ACCESS_KEY/S3_SECRET_KEY.")
PY
chmod 600 "$ENV_FILE"
