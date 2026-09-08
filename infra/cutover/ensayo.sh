#!/usr/bin/env bash
#
# Ensayo del cutover (Paso 5.5).
#
# Corre la secuencia completa del runbook contra el entorno local, cronometrando
# cada etapa. Un procedimiento que nunca se ejecutó no es un procedimiento: es
# una intención.
#
#   infra/cutover/ensayo.sh
#
# NO toca producción. Trabaja contra la base local y la copia del legacy.
set -Eeuo pipefail

cd "$(dirname "$0")/../.."
RAIZ="$PWD"

# shellcheck disable=SC1091
source backend/.venv/bin/activate
set -a; source backend/.env; set +a

: "${LEGACY_DATABASE_URL:?falta LEGACY_DATABASE_URL, apuntando a la copia del legacy}"

PG_URL="${DATABASE_URL/postgresql+asyncpg/postgresql}"
inicio_total=$(date +%s)
fallos=0

paso() {
  local nombre="$1"; shift
  local inicio; inicio=$(date +%s)
  printf '\n▸ %s\n' "$nombre"
  if "$@" > /tmp/ensayo-paso.log 2>&1; then
    printf '  ok · %ss\n' "$(( $(date +%s) - inicio ))"
  else
    printf '  FALLÓ · %ss\n' "$(( $(date +%s) - inicio ))"
    tail -15 /tmp/ensayo-paso.log | sed 's/^/    /'
    fallos=$((fallos + 1))
  fi
}

echo "==============================================================="
echo "ENSAYO DE CUTOVER"
echo "==============================================================="

# --- Paso 1 del runbook: congelar el legacy ---
# En el ensayo se simula, porque la copia local no tiene tráfico. Lo que sí se
# comprueba es que el comando de solo lectura funciona: si el día del cutover
# falla, no hay forma de garantizar que nadie escriba.
paso "Congelar el legacy en solo lectura" bash -c '
  base=$(python - <<PY
from urllib.parse import urlparse
import os
print(urlparse(os.environ["LEGACY_DATABASE_URL"]).path.lstrip("/"))
PY
)
  psql "$LEGACY_DATABASE_URL" -v ON_ERROR_STOP=1 -c \
    "ALTER DATABASE \"$base\" SET default_transaction_read_only = on;"

  # El `SET` de sesión va primero: con la base ya en solo lectura, una conexión
  # nueva nace así y el ALTER para revertirlo falla. Es el error que rompería el
  # rollback el día del cutover, y por eso se ensaya el ida y vuelta completo.
  psql "$LEGACY_DATABASE_URL" -v ON_ERROR_STOP=1 <<SQL
SET default_transaction_read_only = off;
ALTER DATABASE "$base" SET default_transaction_read_only = off;
SQL
'

# --- Paso 3: migrar desde cero ---
paso "Vaciar el destino" psql "$PG_URL" -v ON_ERROR_STOP=1 -c "
  ALTER TABLE shipment_events DISABLE TRIGGER trg_shipment_events_inmutable;
  TRUNCATE legacy_password_pending, legacy_id_map, notification_deliveries, notifications,
           outbox_events, dispatch_events, dispatch_request_shipments, dispatch_documents,
           dispatch_requests, shipment_requirements, shipment_documents, shipment_references,
           shipment_packages, shipment_events, shipments, documents, auth_sessions,
           refresh_tokens, company_memberships, user_role_assignments, users, companies
           RESTART IDENTITY CASCADE;
  ALTER TABLE shipment_events ENABLE TRIGGER trg_shipment_events_inmutable;"

cd backend
paso "Migraciones de esquema" alembic upgrade head
paso "Catálogos" bash -c '
  python -m scripts.seed_rbac
  python -m scripts.seed_shipment_statuses
  python -m scripts.seed_document_types'
paso "Simulación de la migración" python -m scripts.migrate_legacy --dry-run
paso "Migración de datos" python -m scripts.migrate_legacy

# --- Paso 4: validar ---
paso "Conteos origen contra destino" python -m scripts.verificar_migracion
cd "$RAIZ"

# --- Paso 6: pruebas de humo ---
# Se corren contra el backend local. El del día se corre contra producción, pero
# lo que se comprueba es lo mismo.
if curl -fsS http://127.0.0.1:8001/health/ready >/dev/null 2>&1; then
  paso "Prueba de humo: /health/ready" curl -fsS http://127.0.0.1:8001/health/ready
else
  printf '\n▸ Pruebas de humo\n  se saltan: no hay backend en el 8001\n'
fi

# --- Rollback de prueba ---
# La parte que más se olvida ensayar, y la única que se usa cuando algo sale mal.
paso "Rollback: devolver escrituras al legacy" bash -c '
  base=$(python - <<PY
from urllib.parse import urlparse
import os
print(urlparse(os.environ["LEGACY_DATABASE_URL"]).path.lstrip("/"))
PY
)
  psql "$LEGACY_DATABASE_URL" -v ON_ERROR_STOP=1 <<SQL
SET default_transaction_read_only = off;
ALTER DATABASE "$base" SET default_transaction_read_only = off;
SQL
  # Comprobar que de verdad se puede escribir, no solo leer.
  psql "$LEGACY_DATABASE_URL" -v ON_ERROR_STOP=1 -c "CREATE TEMP TABLE _rollback_ok (x int)" > /dev/null'

total=$(( $(date +%s) - inicio_total ))
echo
echo "==============================================================="
printf 'ENSAYO COMPLETO EN %ss\n' "$total"
echo "==============================================================="

if (( fallos > 0 )); then
  echo "$fallos paso(s) fallaron. El runbook no está listo."
  exit 1
fi

echo "Todos los pasos pasaron."
echo
echo "La ventana acordada es de 1 hora. Este ensayo tardó ${total}s sobre datos"
echo "locales; en producción sumá el respaldo final y el cambio de routing."
echo
echo "Lo que este ensayo NO cubre y hay que ensayar aparte:"
echo "  - Cambio de routing en Nginx"
echo "  - Login de un usuario real con su contraseña de producción"
echo "  - Descarga de un documento real desde el storage"
