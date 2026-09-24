"""Verifica que cada FileField de la base legacy tenga un archivo real en disco.

Paso 0.3 del plan de migracion. Correr contra la copia restaurada (amvarmar_restore)
y contra el media/ extraido del tar.gz de 0.2, nunca contra produccion.

Uso:
    python3 docs/migration/check_archivos.py \
        --dsn "postgresql://usuario:password@localhost:5432/amvarmar_restore" \
        --media-root /ruta/a/media \
        --out docs/migration/archivos_faltantes.csv

Requiere: psycopg (pip install "psycopg[binary]")
"""

import argparse
import csv
from pathlib import Path

import psycopg

# (nombre_tabla, columna_file, columna_pk_para_reportar)
TABLAS_CON_ARCHIVO = [
    ("core_warehouse", "uploaded_file", "wr_number"),
    ("core_warehousedocument", "file", "id"),
    ("core_warehouseinvoice", "file", "id"),
    ("core_dispatchbldocument", "file", "id"),
    ("core_adminprofile", "avatar", "id"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True, help="DSN de PostgreSQL (la copia restaurada)")
    parser.add_argument("--media-root", required=True, help="Ruta local a media/ extraido del backup")
    parser.add_argument("--out", default="docs/migration/archivos_faltantes.csv")
    args = parser.parse_args()

    media_root = Path(args.media_root)
    if not media_root.is_dir():
        raise SystemExit(f"No existe el directorio media-root: {media_root}")

    faltantes: list[tuple[str, str, str]] = []
    total_revisados = 0

    with psycopg.connect(args.dsn) as conn, conn.cursor() as cur:
        for tabla, columna, pk in TABLAS_CON_ARCHIVO:
            cur.execute(
                f"SELECT {pk}, {columna} FROM {tabla} "
                f"WHERE {columna} IS NOT NULL AND {columna} <> ''"
            )
            for pk_val, rel_path in cur.fetchall():
                total_revisados += 1
                full_path = media_root / rel_path
                if not full_path.is_file():
                    faltantes.append((tabla, str(pk_val), rel_path))

    with open(args.out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["tabla", "pk", "ruta_relativa"])
        writer.writerows(faltantes)

    print(f"Revisados: {total_revisados}")
    print(f"Faltantes: {len(faltantes)}")
    print(f"Reporte: {args.out}")


if __name__ == "__main__":
    main()
