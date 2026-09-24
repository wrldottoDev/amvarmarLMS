# Despliegue del piloto en la VPS

El LMS corre **en paralelo** con el sistema viejo, en la misma VPS, con una **copia** de sus datos. El
sistema viejo sigue siendo el oficial: nada de este runbook toca su base, sus archivos ni su configuración
de nginx, salvo agregar dos sitios nuevos.

- `app.amvarmar.com` → el LMS (frontend y `/api`).
- `archivos.amvarmar.com` → MinIO, para las URLs firmadas con que el navegador sube y descarga.

En el piloto **el correo va a Mailpit**, no a los clientes: si no, recibirían avisos del piloto y del
sistema viejo por la misma carga.

Los datos marcados **[inspección]** se completan después de revisar la VPS (Paso 0).

## 0. Inspección (solo lectura)

Sistema operativo, CPU, memoria y disco libre; Docker y Compose instalados; qué escucha en cada puerto; cómo
corre el sistema viejo (servicio, base, versión de PostgreSQL, dónde está `media/`) y cómo está su nginx.

Mínimo recomendado para convivir: 4 GB de RAM libres y espacio en disco para la base, los ~10 GB de
archivos migrados y los respaldos.

## 1. Requisitos

- **DNS:** registros A de `app.amvarmar.com` y `archivos.amvarmar.com` hacia la IP de la VPS.
- Docker Engine con el plugin Compose, nginx y certbot en el host.
- El repositorio en `/opt/amvarmar-lms` **[inspección]**, en `main`.

## 2. Configuración

```bash
cd /opt/amvarmar-lms/infra/produccion
cp .env.example .env && chmod 600 .env
# Completar .env. Cada secreto se genera NUEVO para este entorno:
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Si algún puerto de `PUERTO_*` ya está en uso en la VPS, se cambia en `.env` y en los dos archivos de
`nginx/`.

## 3. Levantar

```bash
docker compose --profile piloto up -d --build
docker compose ps        # todo "healthy" salvo beat, que no tiene healthcheck
```

## 4. Base de datos y storage

```bash
docker compose exec backend alembic upgrade head
docker compose exec backend python -m scripts.seed_rbac
docker compose exec backend python -m scripts.seed_shipment_statuses
docker compose exec backend python -m scripts.seed_document_types
# El bucket privado con cifrado; la app no lo crea sola al arrancar.
docker compose exec backend python -c \
  "from app.infrastructure.storage import s3; s3.asegurar_bucket_privado()"
```

## 5. nginx y HTTPS

```bash
sudo cp nginx/*.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/app.amvarmar.com.conf /etc/nginx/sites-enabled/
sudo ln -s /etc/nginx/sites-available/archivos.amvarmar.com.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d app.amvarmar.com -d archivos.amvarmar.com
```

`nginx -t` tiene que pasar **antes** del reload: un error ahí dejaría caído también el sistema viejo.

**Piloto con `app.amvarmar.com` en manos del sistema viejo (caso actual).** El dominio (443) lo sirve el
sistema viejo detrás de Cloudflare y **no se toca**: el piloto va en el mismo dominio, en puertos que
Cloudflare reenvía, con el certificado existente. No se corre certbot ni se instalan los dos sitios de
arriba:

```bash
sudo cp nginx/piloto-lms-8443.conf nginx/piloto-storage-2053.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/piloto-lms-8443.conf /etc/nginx/sites-enabled/
sudo ln -s /etc/nginx/sites-available/piloto-storage-2053.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

En `.env`: `FRONTEND_BASE_URL=https://app.amvarmar.com:8443` y
`S3_ENDPOINT_URL=https://app.amvarmar.com:2053`. Los puertos 8443 y 2053 tienen que estar abiertos en el
firewall del proveedor. Cloudflare corta las peticiones de más de 100 MB en el plan gratuito: los
archivos más grandes no se pueden subir desde el navegador durante el piloto.

## 6. Datos: copia del sistema viejo

```bash
docker compose --profile migracion up -d postgres-legacy
# Volcado de SOLO LECTURA de la base del sistema viejo [inspección: base y usuario]
sudo -u postgres pg_dump -Fc amvarmar_inventory > /tmp/legacy.dump
docker compose cp /tmp/legacy.dump postgres-legacy:/tmp/legacy.dump
docker compose exec postgres-legacy pg_restore -U amvarmar -d amvarmar_legacy --no-owner /tmp/legacy.dump
```

Correcciones del Paso 5.1 **sobre la copia** (registro, correos, cargas sin cliente):

```bash
for f in 000_registro 001_emails 002_cargas_sin_cliente; do
  docker compose cp ../../docs/migration/correcciones/$f.sql postgres-legacy:/tmp/$f.sql
  docker compose exec postgres-legacy psql -U amvarmar -d amvarmar_legacy -v ON_ERROR_STOP=1 -f /tmp/$f.sql
done
```

Migrar, primero en simulación:

```bash
L=postgresql://amvarmar:$POSTGRES_PASSWORD@postgres-legacy:5432/amvarmar_legacy
docker compose run --rm -e LEGACY_DATABASE_URL=$L backend python -m scripts.migrate_legacy --dry-run
docker compose run --rm -e LEGACY_DATABASE_URL=$L backend python -m scripts.migrate_legacy
docker compose run --rm -e LEGACY_DATABASE_URL=$L backend python -m scripts.verificar_migracion --muestra 10
```

Archivos: la carpeta `media/` del sistema viejo ya está en la VPS, así que se monta en **solo lectura** y
no hay que copiar nada [inspección: ruta]:

```bash
M=/ruta/al/media/del/sistema/viejo
docker compose run --rm -v $M:/media-legacy:ro backend python -m scripts.migrate_files --media-root /media-legacy --dry-run
docker compose run --rm -v $M:/media-legacy:ro backend python -m scripts.migrate_files --media-root /media-legacy
docker compose run --rm -v $M:/media-legacy:ro backend python -m scripts.migrate_files --media-root /media-legacy --verificar
```

## 7. Pruebas de humo

1. `https://app.amvarmar.com:8443/login` carga (piloto) y el sistema viejo en `https://app.amvarmar.com` sigue igual.
2. Una cuenta real entra con su contraseña del sistema viejo.
3. El inventario de una empresa coincide con el del sistema viejo.
4. Se descarga un documento migrado.
5. Se sube un documento y pasa de "procesando" a listo (esto prueba el worker).
6. AMVI responde.

Mailpit, para ver los correos del piloto, por túnel SSH:
`ssh -L 58025:127.0.0.1:58025 usuario@vps` y abrir `http://localhost:58025`.

## 8. Respaldo diario

`infra/backup/respaldar.sh` por cron, apuntando al PostgreSQL de este compose (puerto `PUERTO_POSTGRES`,
solo local). Los respaldos quedan fuera de la carpeta del repositorio.

## Volver atrás

El piloto no toca el sistema viejo, así que deshacerlo es apagarlo:

```bash
docker compose --profile piloto --profile migracion down
sudo rm /etc/nginx/sites-enabled/app.amvarmar.com.conf /etc/nginx/sites-enabled/archivos.amvarmar.com.conf
sudo nginx -t && sudo systemctl reload nginx
```

`down -v` borra además los volúmenes con la copia de datos. Se usa solo a propósito.
