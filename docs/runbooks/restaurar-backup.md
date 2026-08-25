# Restaurar un respaldo

**Alertas:** `RespaldoVencido`, `EnsayoDeRestauracionFallido`

**Objetivos comprometidos:** RPO 24 h (se pierde como mucho un día), RTO 4 h.

## Primero: ¿es una emergencia o una alerta preventiva?

`RespaldoVencido` y `EnsayoDeRestauracionFallido` **no** significan que haya que
restaurar ahora. Significan que el respaldo no es de fiar, que es un problema
distinto y se resuelve con calma. Restaurar sin necesidad destruye datos buenos.

Solo se restaura si hay pérdida o corrupción confirmada de datos.

## El respaldo está vencido o el ensayo falló

```bash
# ¿Corrió el job?
systemctl status amvarmar-respaldo.timer
journalctl -u amvarmar-respaldo --since "2 days ago"

# ¿Qué hay en el directorio?
ls -lh "$BACKUP_DIR" | tail -5
df -h "$BACKUP_DIR"   # el disco lleno es la causa más común

# Correr a mano y mirar la salida
BACKUP_DIR=... PGHOST=... PGUSER=... PGDATABASE=... infra/backup/respaldar.sh
```

Con el respaldo nuevo, ensayar antes de darlo por bueno:

```bash
infra/backup/ensayar_restauracion.sh
```

## Restauración real

**Antes de tocar nada:** respaldar el estado actual, aunque esté corrupto. Es la
única forma de volver atrás si la restauración empeora las cosas.

```bash
pg_dump --format=custom --file=/var/backups/antes-de-restaurar-$(date -u +%Y%m%dT%H%M%SZ).dump "$PGDATABASE"
```

1. **Cortar el tráfico.** Parar la API y el worker. Restaurar con escrituras
   entrando deja la base en un estado que no es ni el viejo ni el nuevo.

   ```bash
   systemctl stop amvarmar-api amvarmar-worker
   ```

2. **Elegir el respaldo y verificar su integridad.**

   ```bash
   ls -lh "$BACKUP_DIR"/*.dump | tail -5
   cd "$BACKUP_DIR" && sha256sum -c <archivo>.sha256
   ```

3. **Restaurar en una base nueva, no encima de la actual.** Renombrar al final
   es reversible; restaurar encima no.

   ```bash
   createdb "${PGDATABASE}_restaurada"
   pg_restore --dbname="${PGDATABASE}_restaurada" --no-owner --no-privileges \
              --exit-on-error --jobs=4 <archivo>.dump
   ```

4. **Verificar antes de conmutar.**

   ```bash
   psql -d "${PGDATABASE}_restaurada" -c "SELECT version_num FROM alembic_version"
   psql -d "${PGDATABASE}_restaurada" -c "
     SELECT 'users', count(*) FROM users
     UNION ALL SELECT 'shipments', count(*) FROM shipments
     UNION ALL SELECT 'documents', count(*) FROM documents"
   ```

   Si `alembic_version` no coincide con el código desplegado, correr
   `alembic upgrade head` contra la base restaurada **antes** de conmutar.

5. **Conmutar.**

   ```bash
   psql -c "ALTER DATABASE ${PGDATABASE} RENAME TO ${PGDATABASE}_dañada"
   psql -c "ALTER DATABASE ${PGDATABASE}_restaurada RENAME TO ${PGDATABASE}"
   systemctl start amvarmar-api amvarmar-worker
   curl -fsS localhost:8000/health/ready
   ```

## Los documentos no están en el respaldo

`pg_dump` guarda la base, **no los archivos de S3/MinIO**. Una base restaurada
apunta a `storage_key` que deben seguir existiendo en el bucket. Si se perdió
también el storage, la base queda con referencias a archivos que no están.

El bucket tiene su propio respaldo (versionado del proveedor o réplica) y se
restaura por separado. Restaurar la base a una fecha anterior a la del bucket es
lo correcto: sobran archivos, que es inofensivo. Al revés faltan, que no lo es.

## Después

- Dejar `${PGDATABASE}_dañada` al menos una semana antes de borrarla.
- Anotar cuánto tardó todo. Ese número es el RTO real, no el objetivo.
- Registrar qué se perdió entre el respaldo y el incidente (hasta 24 h por RPO)
  y avisar a quien corresponda.
