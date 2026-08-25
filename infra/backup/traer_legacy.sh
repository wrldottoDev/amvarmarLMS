#!/usr/bin/env bash
#
# Trae la base del sistema viejo desde la VPS y la monta en local (Paso 5.x).
#
# Para qué sirve: poder mirar los datos anteriores y correr el diagnóstico del
# Paso 5.1 sin tocar producción. La base queda como `amvarmar_legacy` en el
# PostgreSQL local, SEPARADA de `amvarmar_lms`: son dos esquemas distintos y
# mezclarlos haría imposible saber qué dato vino de dónde.
#
# Uso:
#   VPS_HOST=usuario@ip infra/backup/traer_legacy.sh
#
# Variables:
#   VPS_HOST        obligatoria. usuario@host de la VPS.
#   VPS_DB          base a copiar en la VPS (por defecto: amvarmar_restore)
#   VPS_USER_DB     usuario de PostgreSQL en la VPS (por defecto: postgres)
#   LOCAL_CONTENEDOR  contenedor local (por defecto: amvarmar-lms-postgres-1)
#   LOCAL_DB        nombre local (por defecto: amvarmar_legacy)
set -Eeuo pipefail

: "${VPS_HOST:?falta VPS_HOST, por ejemplo ubuntu@203.0.113.10}"
VPS_DB="${VPS_DB:-amvarmar_restore}"
VPS_USER_DB="${VPS_USER_DB:-postgres}"
LOCAL_CONTENEDOR="${LOCAL_CONTENEDOR:-amvarmar-lms-postgres-1}"
LOCAL_DB="${LOCAL_DB:-amvarmar_legacy}"

destino="/tmp/${VPS_DB}-$(date -u +%Y%m%dT%H%M%SZ).dump"

echo "1/4  Volcando ${VPS_DB} en ${VPS_HOST}"
# El volcado se hace EN la VPS y viaja por la tubería de ssh: así no hace falta
# abrir el puerto de PostgreSQL a internet ni dejar el archivo en el servidor.
# `--no-owner` y `--no-privileges` evitan que la restauración local falle por
# roles que no existen acá.
ssh "$VPS_HOST" \
    "sudo -u ${VPS_USER_DB} pg_dump --format=custom --compress=9 --no-owner --no-privileges ${VPS_DB}" \
    > "$destino"

tamano="$(du -h "$destino" | cut -f1)"
echo "     recibido: ${destino} (${tamano})"

if [[ ! -s "$destino" ]]; then
  echo "ERROR: el volcado llegó vacío" >&2
  exit 1
fi

echo "2/4  Comprobando que el contenedor local responde"
docker exec "$LOCAL_CONTENEDOR" pg_isready >/dev/null

echo "3/4  Creando ${LOCAL_DB} (se reemplaza si ya existía)"
docker exec "$LOCAL_CONTENEDOR" dropdb --if-exists -U "${POSTGRES_USER:-amvarmar}" "$LOCAL_DB"
docker exec "$LOCAL_CONTENEDOR" createdb -U "${POSTGRES_USER:-amvarmar}" "$LOCAL_DB"

echo "4/4  Restaurando"
docker cp "$destino" "${LOCAL_CONTENEDOR}:/tmp/legacy.dump"
# Sin `--exit-on-error`: un volcado de Django trae objetos cuyo dueño no existe
# acá y esos avisos son esperables. Lo que importa se verifica abajo contando
# filas.
docker exec "$LOCAL_CONTENEDOR" pg_restore \
    -U "${POSTGRES_USER:-amvarmar}" --dbname "$LOCAL_DB" \
    --no-owner --no-privileges "/tmp/legacy.dump" || true
docker exec "$LOCAL_CONTENEDOR" rm -f /tmp/legacy.dump

echo
echo "Conteos de la base restaurada:"
docker exec "$LOCAL_CONTENEDOR" psql -U "${POSTGRES_USER:-amvarmar}" --dbname "$LOCAL_DB" -c "
    SELECT 'auth_user' AS tabla, count(*) FROM auth_user
    UNION ALL SELECT 'core_company', count(*) FROM core_company
    UNION ALL SELECT 'core_warehouse', count(*) FROM core_warehouse
    UNION ALL SELECT 'core_dispatchrequest', count(*) FROM core_dispatchrequest
    UNION ALL SELECT 'core_warehousedocument', count(*) FROM core_warehousedocument"

echo
echo "Listo. La base vieja está en ${LOCAL_DB}, dentro del contenedor local."
echo
echo "Siguiente paso — el diagnóstico del Paso 5.1:"
echo "  docker cp docs/migration/diagnostico_5_1.sql ${LOCAL_CONTENEDOR}:/tmp/"
echo "  docker exec -i ${LOCAL_CONTENEDOR} psql -U ${POSTGRES_USER:-amvarmar} -d ${LOCAL_DB} -f /tmp/diagnostico_5_1.sql"
echo
echo "El volcado local queda en ${destino}."
echo "Borralo cuando termines: son datos reales de clientes."
