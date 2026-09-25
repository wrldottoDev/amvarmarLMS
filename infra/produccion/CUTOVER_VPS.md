# Cutover en la VPS: el LMS pasa a app.amvarmar.com

El sistema viejo se congela, se migra **desde cero** al LMS para que refleje su estado exacto, el dominio
pasa al LMS y el viejo queda **apagado, sin borrar**. El borrado es un paso aparte (C9) y requiere la
confirmación explícita de Otto después de verificar todo y de tener un respaldo fuera de la VPS.

Se ejecuta en `/opt/amvarmar-lms/infra/produccion` salvo que se indique otra cosa. Cada paso que cambie
algo se confirma con Otto. Al final de cada fase: reporte y pausa.

## Límites

- La base y los archivos del sistema viejo **solo se leen** hasta C9.
- El sitio de nginx del viejo no se borra: solo se quita su enlace de `sites-enabled`.
- `sudo nginx -t` antes de cada reload.
- Los secretos no se imprimen. La contraseña SMTP y la clave de OpenAI las escribe Otto en `.env`.
- Si algo falla antes de C6, se aplica **Volver atrás** y el viejo sigue siendo el oficial.

## C0 — Preparación (sin corte)

1. `git checkout main && git pull`.
2. Otros procesos del viejo que escriban en su base: `systemctl list-units | grep -i -E 'celery|amvarmar'`,
   `crontab -l` (de ubuntu y de root). Anotarlos para detenerlos en C1.
3. Existen `/etc/letsencrypt/options-ssl-nginx.conf` y `/etc/letsencrypt/ssl-dhparams.pem`. Si alguno
   falta, quitar su línea de `nginx/produccion-app.amvarmar.com.conf` antes de usarlo.
4. Otto escribe en `.env` la `OPENAI_API_KEY` y la `SMTP_PASSWORD` de `pricing1@amvarmar.com`.

**Reporte** y pausa. Otto elige el momento del corte: desde C1 el sistema viejo deja de responder.

## C1 — Congelar el viejo

```bash
sudo systemctl stop gunicorn          # y los procesos anotados en C0.2
```

Desde acá nadie puede cargar nada en el viejo (Cloudflare muestra un error hasta C6).

## C2 — Respaldo final del viejo

En `/opt/respaldos/legacy-final-AAAAMMDDTHHMM/`:

- `pg_dump -Fc` de `amvarmar_inventory` (`sudo -u postgres`), su `.sha256` y `pg_restore --list` para
  validarlo.
- Conteos de todas las tablas del viejo, que se usan en C4: `SELECT relname, n_live_tup` no alcanza;
  hacer `count(*)` real de `auth_user`, `core_company`, `core_clientprofile`, `core_warehouse`,
  `core_piecewarehouse`, `core_dispatchrequest`, `core_dispatchrequestitem`, `core_warehousedocument`,
  `core_warehouseinvoice` y `core_dispatchbldocument`.
- `media/`: `rsync -a --delete` sobre la copia de `/opt/respaldos/legacy-20260924/media` (solo trae lo
  nuevo) y un inventario sha256 nuevo de todos los archivos.

## C3 — Migrar desde cero

Todo lo del piloto se descarta. Su base ya tiene respaldo en `/opt/respaldos/lms-*`.

```bash
docker compose stop backend worker beat frontend
docker compose exec postgres dropdb -U amvarmar amvarmar_lms
docker compose exec postgres createdb -U amvarmar amvarmar_lms
docker compose rm -sf minio && docker volume rm amvarmar-lms-prod_minio-data
docker compose up -d minio backend
docker compose exec backend alembic upgrade head
docker compose exec backend python -m scripts.seed_rbac
docker compose exec backend python -m scripts.seed_shipment_statuses
docker compose exec backend python -m scripts.seed_document_types
docker compose exec backend python -c "from app.infrastructure.storage import s3; s3.asegurar_bucket_privado()"
```

Copia del viejo, restaurada desde el volcado de C2:

```bash
docker compose --profile migracion up -d postgres-legacy
docker compose exec postgres-legacy dropdb -U amvarmar amvarmar_legacy
docker compose exec postgres-legacy createdb -U amvarmar amvarmar_legacy
docker compose cp <dump de C2> postgres-legacy:/tmp/legacy.dump
docker compose exec postgres-legacy pg_restore -U amvarmar -d amvarmar_legacy --no-owner --no-privileges /tmp/legacy.dump
```

Correcciones del 5.1 sobre la copia (`000_registro`, `001_emails`, `002_cargas_sin_cliente`). Si
`001_emails` aborta por un correo vacío o duplicado nuevo, **pausa**: hace falta una decisión.

Migrador: `--dry-run`, la corrida real y `verificar_migracion --muestra 10` (misma orden que en el
piloto, con la URL armada dentro del contenedor). Archivos: `migrate_files` con `media/` montada `:ro`,
la corrida real y `--verificar`.

## C4 — Verificación total (gate para seguir)

Todo tiene que dar **cero diferencias**:

- `verificar_migracion`: todos los conteos cuadran.
- Los conteos del viejo de C2 contra el LMS vía `legacy_id_map`: usuarios, empresas, cargas, piezas,
  despachos, cargas en despachos y documentos.
- Cargas por empresa, viejo contra LMS: iguales en todas.
- `documents`: todos `READY`. `migrate_files --verificar`: 0 faltantes y 0 discrepancias.
- Respaldo de la base del LMS migrada: `lms-cutover-AAAAMMDD.dump` + sha256.

Si algo no cuadra: **pausa** y Volver atrás.

## C5 — Cambio de dominio y correo real

`.env`:

```
FRONTEND_BASE_URL=https://app.amvarmar.com
SMTP_HOST=mail.amvarmar.com
SMTP_PORT=465
SMTP_USE_SSL=true
SMTP_USE_TLS=false
SMTP_USERNAME=pricing1@amvarmar.com
# SMTP_PASSWORD la escribió Otto en C0
```

```bash
docker compose --profile piloto stop mailpit
docker compose up -d --force-recreate minio backend worker beat frontend
```

nginx (el sitio viejo se respalda y se desenlaza, no se borra):

```bash
sudo cp /etc/nginx/sites-available/app.amvarmar.com /opt/respaldos/nginx-app.amvarmar.com.viejo
sudo cp nginx/produccion-app.amvarmar.com.conf /etc/nginx/sites-available/
sudo rm /etc/nginx/sites-enabled/app.amvarmar.com
sudo rm /etc/nginx/sites-enabled/piloto-lms-8443.conf
sudo ln -s /etc/nginx/sites-available/produccion-app.amvarmar.com.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

El storage sigue en `https://app.amvarmar.com:2053` (`piloto-storage-2053.conf` queda activo).

## C6 — Pruebas en producción

1. `https://app.amvarmar.com/login` da 200 y es el LMS.
2. Otto entra con su contraseña del sistema viejo y ve su inventario.
3. Descarga un documento migrado y sube uno nuevo, que pasa a disponible.
4. AMVI responde.
5. Correo real: Otto pide "¿Olvidaste tu contraseña?" y le llega el correo.

Si 1, 2 o 3 fallan: Volver atrás.

Con todo en verde, el viejo queda deshabilitado (no borrado): `sudo systemctl disable gunicorn`.

## C7 — Respaldo fuera de la VPS

Desde la Mac de Otto, con `rsync` por SSH, a `~/amvarmar-respaldos/`: el volcado final del viejo con su
sha256, la copia de `media/` con su inventario y el volcado del LMS de C4. Se verifica cada sha256 en
la Mac.

## C8 — Estabilización

El viejo queda apagado con sus datos intactos. Revisar a diario los logs del worker, las entregas de
correo (`notification_deliveries` en `FAILED`) y el respaldo diario.

## C9 — Borrado del sistema viejo (aparte, con confirmación explícita)

Solo cuando: C4 dio cero diferencias, C7 está verificado en la Mac y Otto lo confirma por escrito.
Recién ahí: `dropdb` de `amvarmar_inventory` y `amvarmar_restore`, borrar `/home/ubuntu/amvarmar`, el
servicio `gunicorn.service` y el sitio de nginx del viejo. La copia de `media/` de `/opt/respaldos` se
puede liberar después de verificar C7.

## Volver atrás (antes de C9)

```bash
sudo rm /etc/nginx/sites-enabled/produccion-app.amvarmar.com.conf
sudo ln -s /etc/nginx/sites-available/app.amvarmar.com /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl start gunicorn
```

La base y los archivos del viejo no se tocaron: vuelve exactamente como estaba.
