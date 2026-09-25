#!/usr/bin/env bash
#
# Vigilancia de la VPS, cada hora por cron. Manda un correo si:
#   - el disco pasa de UMBRAL_DISCO (por defecto 90 %);
#   - el respaldo de la base no dejó un volcado en las últimas 26 h;
#   - el respaldo de archivos no terminó bien en las últimas 26 h.
#
# Un respaldo que falla en silencio es tan grave como no tenerlo: nadie lo
# nota hasta que hace falta restaurar.
#
# Cada problema avisa como mucho una vez por día (estado en ESTADO_DIR), para
# no llenar la casilla mientras se resuelve. El correo sale con el SMTP del LMS
# (scripts/enviar_alerta.py dentro del contenedor del backend), a ALERTA_CORREO
# del .env.
#
#   0 * * * * COMPOSE_DIR=/opt/amvarmar-lms/infra/produccion /opt/amvarmar-lms/infra/backup/vigilar.sh >> /home/ubuntu/vigilar.log 2>&1
set -Eeuo pipefail

: "${COMPOSE_DIR:?falta COMPOSE_DIR}"
ENV_FILE="${ENV_FILE:-${COMPOSE_DIR}/.env}"
UMBRAL_DISCO="${UMBRAL_DISCO:-90}"
RESPALDO_BASE_DIR="${RESPALDO_BASE_DIR:-/opt/respaldos/lms-diario}"
RESPALDO_ARCHIVOS_DIR="${RESPALDO_ARCHIVOS_DIR:-/opt/respaldos/archivos}"
MAX_HORAS="${MAX_HORAS:-26}"
ESTADO_DIR="${ESTADO_DIR:-${HOME}/.vigilar}"

leer_env() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }
destino="$(leer_env ALERTA_CORREO)"
[[ -n "$destino" ]] || { echo "ERROR: falta ALERTA_CORREO en ${ENV_FILE}" >&2; exit 1; }

mkdir -p "$ESTADO_DIR"
ahora="$(date -u +%Y-%m-%dT%H:%MZ)"

alertar() {
  local codigo="$1" asunto="$2" texto="$3"
  local marca="${ESTADO_DIR}/${codigo}"
  # Ya se avisó en las últimas 24 h: no repetir.
  if [[ -n "$(find "$marca" -mmin -1440 2>/dev/null)" ]]; then
    echo "${ahora} ${codigo}: sigue (ya avisado)"
    return 0
  fi
  # El compose usa su propio .env (el de COMPOSE_DIR): ENV_FILE solo aporta ALERTA_CORREO.
  if docker compose --project-directory "$COMPOSE_DIR" exec -T backend \
      python -m scripts.enviar_alerta --para "$destino" --asunto "$asunto" --texto "$texto"; then
    touch "$marca"
    echo "${ahora} ${codigo}: avisado"
  else
    echo "${ahora} ${codigo}: NO se pudo mandar el correo" >&2
  fi
}

resuelto() { rm -f "${ESTADO_DIR}/$1"; }

# Hace cuántas horas se modificó el archivo más nuevo que coincide (vacío si no hay).
horas_desde() {
  local mas_nuevo
  mas_nuevo="$(find "$@" -type f -printf '%T@\n' 2>/dev/null | sort -n | tail -1)"
  [[ -n "$mas_nuevo" ]] || return 0
  echo $(( ($(date +%s) - ${mas_nuevo%.*}) / 3600 ))
}

# --- Disco ---
uso="$(df --output=pcent / | tail -1 | tr -dc '0-9')"
if (( uso >= UMBRAL_DISCO )); then
  alertar disco "Disco al ${uso} %" "El disco de la VPS está al ${uso} % (umbral ${UMBRAL_DISCO} %).

$(df -h /)

Lo que más ocupa en /opt/respaldos:
$(du -sh /opt/respaldos/* 2>/dev/null | sort -h | tail -5)

Ver docs/runbooks/storage-garage.md, \"Espacio en disco\"."
else
  resuelto disco
fi

# --- Respaldo de la base ---
horas="$(horas_desde "$RESPALDO_BASE_DIR" -name '*.dump')"
if [[ -z "$horas" || "$horas" -ge "$MAX_HORAS" ]]; then
  alertar respaldo-base "El respaldo de la base no corrió" "No hay un volcado de la base de menos de ${MAX_HORAS} h en ${RESPALDO_BASE_DIR} (el más nuevo: ${horas:-ninguno} h).

Revisar el cron de las 03:30 y /home/ubuntu/respaldo-lms.log."
else
  resuelto respaldo-base
fi

# --- Respaldo de archivos ---
horas="$(horas_desde "${RESPALDO_ARCHIVOS_DIR}/ultimo-ok")"
if [[ -z "$horas" || "$horas" -ge "$MAX_HORAS" ]]; then
  alertar respaldo-archivos "El respaldo de archivos no terminó bien" "El respaldo de archivos no termina bien desde hace ${horas:-(nunca)} h (límite ${MAX_HORAS} h).

Revisar el cron de las 04:15 y /home/ubuntu/respaldo-archivos.log."
else
  resuelto respaldo-archivos
fi

echo "${ahora} ok: disco ${uso} %"
