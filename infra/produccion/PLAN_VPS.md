# Plan para desplegar el piloto en la VPS

Instrucciones para quien ejecuta el despliegue **en la VPS**, sea una persona o un asistente. El detalle
de cada comando está en `docs/runbooks/despliegue-piloto.md`; esto es el orden, los límites y qué
reportar en cada paso.

## Contexto

- AMVARMAR LMS reemplaza al sistema viejo (Django, `amvarmarProduccion`), que **hoy corre en esta misma
  VPS y es el sistema oficial**.
- Esto es un **piloto en paralelo**: el LMS se levanta al lado, con una **copia** de los datos, en
  `app.amvarmar.com` (y `archivos.amvarmar.com` para el storage). El sistema viejo sigue igual.
- En el piloto el correo va a Mailpit: ningún cliente recibe nada.

## Límites (no negociables)

1. **No tocar el sistema viejo:** su base (solo `pg_dump` de lectura), sus archivos (solo montaje en
   solo lectura), su servicio y su sitio de nginx. No reiniciarlo.
2. **Nada de comandos destructivos** sin confirmación explícita de la persona: `docker system prune`,
   `docker compose down -v`, `rm -rf`, `DROP`, cambios de firewall, reinicio del servidor.
3. **Los secretos no se imprimen ni se pegan en el chat.** Se generan directo al `.env` con
   `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`; la clave de OpenAI la escribe la
   persona en el archivo con un editor.
4. **`sudo nginx -t` antes de cualquier reload.** Un error ahí tira también el sistema viejo.
5. **Detenerse al final de cada fase** y mostrar el reporte. No se pasa a la siguiente sin un "seguí".

## Fases

### F0 — Repositorio
Clonar `wrldottoDev/amvarmarLMS` en `/opt/amvarmar-lms` (o donde indique la persona). Es privado:
usar `gh auth login` o una deploy key **de solo lectura**. Rama: `main`.
**Reporte:** ruta y commit (`git log -1 --oneline`).

### F1 — Inspección (solo lectura, no se cambia nada)
- Sistema operativo y versión; CPU, memoria total y libre (`free -h`), disco (`df -h`).
- Docker y Compose (`docker --version`, `docker compose version`); qué contenedores corren.
- Puertos en uso (`sudo ss -tlnp`): confirmar que 53000, 55432, 58000, 58025, 59000 y 59001 estén libres.
- Sistema viejo: cómo corre (systemd, docker, gunicorn), versión de PostgreSQL, nombre de la base y
  usuario, y **ruta de `media/`** (`MEDIA_ROOT`).
- nginx: sitios en `sites-enabled`, cuál sirve al sistema viejo, certbot instalado o no.
- DNS: `dig +short app.amvarmar.com archivos.amvarmar.com` contra la IP pública de la VPS.

**Reporte:** todo lo anterior, más cualquier riesgo que se vea (poca memoria, puertos tomados, falta
Docker). **Detenerse.**

### F2 — Preparación
Instalar lo que falte (Docker, plugin Compose, certbot) **solo con confirmación**. Crear
`infra/produccion/.env` desde `.env.example`, permisos 600, secretos generados en el archivo. Ajustar
`PUERTO_*` y `nginx/*.conf` si hubo choques.
**Reporte:** qué se instaló y qué variables quedan vacías (sin mostrar valores).

### F3 — Levantar el stack
`docker compose --profile piloto up -d --build`, migraciones, seeds y bucket (runbook, pasos 3 y 4).
**Reporte:** `docker compose ps` y la salida de `alembic current`.

### F4 — nginx y HTTPS
Copiar los dos sitios, `nginx -t`, reload y certbot (runbook, paso 5). Requiere el DNS ya apuntando.
**Reporte:** `curl -sI https://app.amvarmar.com/login` y que el sitio del sistema viejo siga respondiendo
igual que antes.

### F5 — Copia de datos y migración
Volcado de **lectura** del legacy, restauración en `postgres-legacy`, correcciones del 5.1 **sobre la
copia**, migrador con `--dry-run` primero y verificación (runbook, paso 6).
**Reporte:** el reporte del migrador (tablas, problemas y avisos) y el de `verificar_migracion`.
**Detenerse** antes de la corrida real si el dry-run muestra problemas.

### F6 — Archivos
`migrate_files` con `media/` del sistema viejo montado en solo lectura: `--dry-run`, corrida real y
`--verificar`. Es la etapa larga.
**Reporte:** faltantes y discrepancias (la meta es cero).

### F7 — Pruebas y respaldo
Las seis pruebas de humo del runbook (paso 7) y el cron de respaldo diario (paso 8).
**Reporte:** resultado de cada prueba.

## Si algo sale mal

Detenerse y reportar el error completo. El piloto no toca el sistema viejo, así que la vuelta atrás es
apagarlo (runbook, "Volver atrás"). No improvisar arreglos sobre el sistema viejo.
