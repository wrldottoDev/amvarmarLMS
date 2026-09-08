#!/usr/bin/env bash
#
# Ensayo de restauración (Paso 4.3).
#
# Un respaldo que nunca se restauró no es un respaldo: es un archivo. Este
# script toma el más reciente, lo restaura en una base desechable y comprueba
# que los datos están. Corre programado, no solo cuando hay una emergencia.
#
# RTO objetivo: 4 h. El tiempo que tarde esto es el dato real para saber si ese
# objetivo se cumple; se imprime al final.
set -Eeuo pipefail

: "${PGHOST:?falta PGHOST}"
: "${PGUSER:?falta PGUSER}"
: "${PGDATABASE:?falta PGDATABASE}"
: "${BACKUP_DIR:?falta BACKUP_DIR}"

# Edad máxima aceptable del respaldo, en horas. Coincide con el RPO de 24 h.
MAX_EDAD_HORAS="${MAX_EDAD_HORAS:-26}"
base_ensayo="${PGDATABASE}_ensayo_$(date -u +%Y%m%d%H%M%S)"
JOB_NOMBRE="amvarmar_ensayo_restauracion"

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

# Cualquier salida por error publica el fallo. Sin esto, un ensayo que muere a
# mitad dejaría la métrica en el "1" del ensayo anterior y la alerta callada,
# que es peor que no tener métrica.
fallar() {
  publicar_metrica amvarmar_ensayo_restauracion_correcto 0 \
    "1 si el último ensayo de restauración fue correcto."
}
trap fallar ERR

ultimo="$(find "$BACKUP_DIR" -name "${PGDATABASE}-*.dump" -type f | sort | tail -1)"
if [[ -z "$ultimo" ]]; then
  echo "ERROR: no hay ningún respaldo en ${BACKUP_DIR}" >&2
  exit 1
fi

echo "ensayando con: $ultimo"

# 1. ¿El respaldo es reciente? Un ensayo que pasa contra un archivo de hace un
#    mes da una falsa sensación de seguridad.
edad_segundos=$(( $(date -u +%s) - $(date -u -r "$ultimo" +%s) ))
if (( edad_segundos > MAX_EDAD_HORAS * 3600 )); then
  echo "ERROR: el respaldo más reciente tiene $(( edad_segundos / 3600 )) h;" \
       "el máximo aceptable es ${MAX_EDAD_HORAS} h" >&2
  exit 1
fi

# 2. ¿Está íntegro? Se verifica antes de intentar restaurar.
if [[ -f "${ultimo}.sha256" ]]; then
  (cd "$(dirname "$ultimo")" && sha256sum -c "$(basename "${ultimo}.sha256")")
else
  echo "AVISO: el respaldo no tiene suma de control" >&2
fi

inicio=$(date -u +%s)
limpiar() { dropdb --if-exists "$base_ensayo" || true; }
trap limpiar EXIT

createdb "$base_ensayo"

# `--exit-on-error`: sin esto, pg_restore informa los fallos y termina con
# éxito, y el ensayo pasaría con una base a medio restaurar.
pg_restore --dbname="$base_ensayo" --no-owner --no-privileges --exit-on-error "$ultimo"

# 3. ¿Los datos están de verdad? Contar filas de las tablas que no pueden estar
#    vacías en un sistema en uso. Restaurar solo el esquema también "funciona".
faltantes=0
for tabla in users companies shipments audit_logs; do
  filas=$(psql --dbname="$base_ensayo" -tAc "SELECT count(*) FROM ${tabla}")
  echo "  ${tabla}: ${filas} filas"
  if [[ "$filas" == "0" ]]; then
    echo "  ERROR: ${tabla} quedó vacía tras restaurar" >&2
    faltantes=$((faltantes + 1))
  fi
done

# 4. ¿Las migraciones quedaron en la misma versión? Restaurar una base con un
#    esquema viejo dejaría la aplicación sin arrancar.
version=$(psql --dbname="$base_ensayo" -tAc "SELECT version_num FROM alembic_version")
echo "  alembic_version: ${version}"

duracion=$(( $(date -u +%s) - inicio ))
echo "restauración completa en ${duracion}s (RTO objetivo: 4 h)"

if (( faltantes > 0 )); then
  echo "ENSAYO FALLIDO: ${faltantes} tabla(s) sin datos" >&2
  fallar
  exit 1
fi
publicar_metrica amvarmar_ensayo_restauracion_correcto 1 \
  "1 si el último ensayo de restauración fue correcto."
publicar_metrica amvarmar_ensayo_restauracion_duracion_segundos "$duracion" \
  "Duración del último ensayo de restauración (insumo del RTO)."
trap - ERR
echo "ENSAYO CORRECTO"
