#!/usr/bin/env bash
#
# Respaldo de PostgreSQL (Paso 4.3).
#
# RPO objetivo: 24 h. Este script corre a diario; si se pierde la base, lo
# máximo que se pierde es un día de trabajo.
#
# Formato `custom` (-Fc), no SQL plano: permite restaurar tablas sueltas,
# comprime, y `pg_restore` puede paralelizar. Un .sql plano de esta base tarda
# horas en volver a entrar y no deja restaurar solo una tabla.
set -Eeuo pipefail

: "${PGHOST:?falta PGHOST}"
: "${PGUSER:?falta PGUSER}"
: "${PGDATABASE:?falta PGDATABASE}"
: "${BACKUP_DIR:?falta BACKUP_DIR}"
RETENCION_DIAS="${RETENCION_DIAS:-30}"
JOB_NOMBRE="amvarmar_respaldo"

# Publica una métrica en Pushgateway. Los trabajos programados no pueden ser
# raspados —terminan antes del siguiente raspado—, así que empujan su resultado.
# Si Pushgateway no responde no se aborta: el respaldo ya está hecho y perder la
# métrica es menos grave que perder el respaldo.
publicar_metrica() {
  local nombre="$1" valor="$2" ayuda="$3"
  if [[ -z "${PUSHGATEWAY_URL:-}" ]]; then
    return 0
  fi
  printf '# HELP %s %s\n# TYPE %s gauge\n%s %s\n' \
    "$nombre" "$ayuda" "$nombre" "$nombre" "$valor" \
    | curl --silent --show-error --max-time 10 --data-binary @- \
        "${PUSHGATEWAY_URL}/metrics/job/${JOB_NOMBRE}/instance/${PGDATABASE}" \
    || echo "AVISO: no se pudo publicar la métrica ${nombre}" >&2
}

marca="$(date -u +%Y%m%dT%H%M%SZ)"
destino="${BACKUP_DIR}/${PGDATABASE}-${marca}.dump"

mkdir -p "$BACKUP_DIR"

# `pg_dump` se niega a respaldar un servidor MÁS NUEVO que él, y el error que
# da no dice qué hacer. Se comprueba antes para que el fallo sea accionable:
# una máquina de respaldos con herramientas viejas produce cero respaldos y
# nadie lo nota hasta que hace falta restaurar.
# Toma el primer campo que parezca un número de versión. No sirve `$NF`:
# Homebrew imprime "pg_dump (PostgreSQL) 14.20 (Homebrew)" y el último campo
# es "(Homebrew)". Tampoco `grep | head`: con `pipefail`, cerrar la tubería
# antes de tiempo manda SIGPIPE al productor y el script aborta con 141.
version_mayor() {
  awk '{for (i = 1; i <= NF; i++) if ($i ~ /^[0-9]+(\.[0-9]+)*$/) { split($i, v, "."); print v[1]; exit }}'
}

version_cliente="$(pg_dump --version | version_mayor)"
version_servidor="$(psql -tAc 'SHOW server_version' | version_mayor)"

if [[ -z "$version_cliente" || -z "$version_servidor" ]]; then
  echo "AVISO: no se pudo determinar la versión de pg_dump o del servidor" >&2
elif (( version_cliente < version_servidor )); then
  echo "ERROR: pg_dump es de PostgreSQL ${version_cliente} y el servidor es ${version_servidor}." >&2
  echo "       pg_dump no puede respaldar un servidor más nuevo que él." >&2
  echo "       Instale las herramientas cliente de PostgreSQL ${version_servidor} o superior." >&2
  exit 1
fi

echo "[$(date -u +%FT%TZ)] respaldando ${PGDATABASE} -> ${destino}"

# Se escribe a un temporal y se renombra al final: un respaldo interrumpido no
# debe quedar con el nombre definitivo, o la próxima restauración usaría un
# archivo truncado creyéndolo bueno.
pg_dump --format=custom --compress=9 --no-owner --no-privileges \
        --file="${destino}.parcial" "$PGDATABASE"
mv "${destino}.parcial" "$destino"

# La suma de control se calcula una sola vez, acá. Verificarla antes de
# restaurar detecta corrupción en reposo o en el traslado.
sha256sum "$destino" > "${destino}.sha256"

echo "[$(date -u +%FT%TZ)] listo: $(du -h "$destino" | cut -f1)"

# La alerta RespaldoVencido mira esta marca. Se publica solo tras renombrar el
# temporal: un respaldo a medias no debe contar como éxito.
publicar_metrica amvarmar_respaldo_ultimo_exito_timestamp "$(date -u +%s)" \
  "Marca de tiempo del último respaldo correcto."
publicar_metrica amvarmar_respaldo_tamano_bytes "$(stat -f%z "$destino" 2>/dev/null || stat -c%s "$destino")" \
  "Tamaño del último respaldo."

# Purga por antigüedad. Se hace DESPUÉS de que el respaldo nuevo esté completo:
# borrar primero dejaría una ventana sin ningún respaldo válido.
find "$BACKUP_DIR" -name "${PGDATABASE}-*.dump" -mtime "+${RETENCION_DIAS}" -print -delete
find "$BACKUP_DIR" -name "${PGDATABASE}-*.dump.sha256" -mtime "+${RETENCION_DIAS}" -delete

# Un `.parcial` viejo es un respaldo que murió a mitad; se limpia para que no
# ocupe disco en silencio.
find "$BACKUP_DIR" -name "*.parcial" -mtime +1 -print -delete
