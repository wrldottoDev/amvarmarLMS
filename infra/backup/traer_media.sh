#!/usr/bin/env bash
#
# Trae la carpeta `media/` del sistema viejo (Paso 5.3).
#
# Son cerca de 10 GB. Se usa `rsync` y no `scp` por una razón concreta: se puede
# cortar y reanudar sin volver a empezar, que con este volumen deja de ser un
# detalle.
#
#   VPS_HOST=usuario@ip infra/backup/traer_media.sh
#
# Variables:
#   VPS_HOST     obligatoria. usuario@host de la VPS.
#   MEDIA_REMOTO ruta en la VPS (por defecto la del inventario del Paso 0.3)
#   MEDIA_LOCAL  destino local (por defecto ./media-legacy)
set -Eeuo pipefail

: "${VPS_HOST:?falta VPS_HOST, por ejemplo ubuntu@203.0.113.10}"
MEDIA_REMOTO="${MEDIA_REMOTO:-/home/ubuntu/amvarmar/amvarmarProduccion/media}"
MEDIA_LOCAL="${MEDIA_LOCAL:-./media-legacy}"

mkdir -p "$MEDIA_LOCAL"

echo "Copiando ${VPS_HOST}:${MEDIA_REMOTO} -> ${MEDIA_LOCAL}"
echo "Se puede cortar con Ctrl-C y volver a lanzar: continúa donde quedó."
echo

# --partial guarda los archivos a medias para poder retomarlos.
# --times conserva las fechas, que son parte del expediente.
# Sin --delete: esto copia, no sincroniza. Borrar en local algo que ya no está
# en la VPS sería destruir la única copia que queda.
rsync --archive --partial --progress --human-readable --times \
      "${VPS_HOST}:${MEDIA_REMOTO}/" "${MEDIA_LOCAL}/"

echo
echo "Copiado. Tamaño local:"
du -sh "$MEDIA_LOCAL"

echo
echo "Subcarpetas encontradas:"
find "$MEDIA_LOCAL" -maxdepth 1 -mindepth 1 -type d -exec basename {} \; | sort

echo
echo "Siguiente paso — simular la subida antes de escribir nada:"
echo "  cd backend && python -m scripts.migrate_files --media-root ../${MEDIA_LOCAL#./} --dry-run"
echo
echo "Estos archivos son documentos de clientes. Borrá la copia local cuando"
echo "el cutover termine y el storage esté verificado."
