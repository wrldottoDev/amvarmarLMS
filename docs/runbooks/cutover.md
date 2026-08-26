# Cutover: pasar de sistema viejo a nuevo

**Pasos 5.5 y 5.6.** El día del cambio nadie improvisa: acá está la secuencia,
los criterios para abortar y cómo volver atrás.

## Antes de leer nada más

**Esto es un corte, no una convivencia.** Django y FastAPI no deben escribir a la
vez sobre los mismos datos. En cuanto el sistema viejo pasa a solo lectura,
queda así hasta que el cutover termine o se aborte.

**El rollback existe y hay que estar dispuesto a usarlo.** Un cutover que "casi
funciona" es peor que uno abortado: los clientes empiezan a operar sobre datos
en los que nadie confía.

## Quién hace qué

Tres personas, y no es negociable: quien ejecuta no puede verificar su propio
trabajo a las 2 de la mañana.

| Rol | Responsabilidad |
|---|---|
| **Ejecuta** | Corre los comandos. No decide nada. |
| **Verifica** | Comprueba cada paso antes de dar el siguiente. Tiene voz de "pará". |
| **Comunica** | Habla con los clientes y con la operación. No toca comandos. |
| **Decide el rollback** | _(nombre y suplente — llenar antes del día)_ |

Toda la sesión se graba: `script -a cutover-$(date -u +%Y%m%dT%H%M%SZ).log`

## Ventana

Los tiempos vienen del ensayo del Paso 5.4 sobre el backup real (241 cargas,
268 documentos):

| Etapa | Medido | Con margen |
|---|---|---|
| Respaldo final del legacy | ~1 min | 5 min |
| Migraciones de esquema | 1 s | 2 min |
| Migración de datos | 3 s | 5 min |
| Verificación de conteos | 1 s | 3 min |
| Cambio de routing + pruebas de humo | — | 15 min |
| **Total** | | **30 min** |

**La ventana acordada es de 1 hora**, el doble de lo estimado. Si a los 45
minutos las pruebas de humo no pasan, se ejecuta el rollback: alargar la ventana
"un poquito más" es como se convierte una hora en seis.

Los archivos NO entran en la ventana. Se suben antes (Paso 5.3), con el sistema
viejo funcionando: son cerca de 10 GB y no hay razón para tener a nadie parado
mientras se copian.

_Fecha y hora acordadas: **llenar antes del día**. Recomendación: día de menor
movimiento, con la operación de AMVARMAR disponible._

## Comunicación previa

- **72 horas antes:** aviso a los clientes con fecha, hora y duración.
- **24 horas antes:** recordatorio.
- **Al empezar:** aviso de que el sistema queda en solo lectura.
- **Al terminar:** aviso de que ya se puede operar, con el enlace nuevo y una
  línea sobre qué cambió.
- **Si se aborta:** aviso de que se sigue en el sistema de siempre y que se
  reagenda. Sin explicaciones técnicas.

## Secuencia

### 1. Congelar el sistema viejo (T+0)

```bash
# Solo lectura a nivel de base: más confiable que confiar en que nadie entre.
sudo -u postgres psql -d amvarmar_inventory -c \
  "ALTER DATABASE amvarmar_inventory SET default_transaction_read_only = on;"
sudo systemctl restart amvarmar-legacy   # para que tome el cambio
```

**Antes de seguir, comprobá que sabés deshacerlo.** Una vez puesto el modo, una
conexión nueva a esa base nace en solo lectura, así que `ALTER DATABASE` para
quitarlo **también falla**. Hay que desactivarlo primero en la sesión:

```bash
sudo -u postgres psql -d amvarmar_inventory <<'SQL'
SET default_transaction_read_only = off;
ALTER DATABASE amvarmar_inventory SET default_transaction_read_only = off;
SQL
```

Se descubrió ensayando: sin ese `SET` previo, el rollback falla exactamente
cuando hace falta. Volvé a ponerlo en solo lectura antes de continuar.

**Verificar:** entrar al sistema viejo e intentar crear una carga. Debe fallar.
Si no falla, **pará acá**: hay una conexión escribiendo por otro lado.

### 2. Respaldo final (T+2)

```bash
sudo -u postgres pg_dump --format=custom --compress=9 --no-owner --no-privileges \
  amvarmar_inventory > /var/backups/cutover-$(date -u +%Y%m%dT%H%M%SZ).dump
sha256sum /var/backups/cutover-*.dump | tee -a /var/backups/cutover.sha256
```

**Este archivo es el punto de retorno.** Sin él no hay rollback posible.

**Verificar:** el tamaño es parecido al del último respaldo diario, y la suma de
control se escribió.

### 3. Migrar (T+7)

El sistema nuevo se migra **desde cero**, no en delta. Con este volumen tarda
segundos, y un delta tendría que detectar además lo que cambió desde el ensayo —
el migrador es idempotente, así que saltaría las cargas ya migradas aunque su
estado hubiera cambiado, y esa desincronización no daría ningún error.

```bash
cd /opt/amvarmar-lms/backend
alembic upgrade head
python -m scripts.seed_rbac
python -m scripts.seed_shipment_statuses
python -m scripts.seed_document_types

# Los archivos ya subidos NO se tocan: `documents` conserva su `storage_key`
# porque el migrador no borra, y `legacy_id_map` se rehace junto con los datos.
python -m scripts.migrate_legacy --dry-run   # primero en seco
python -m scripts.migrate_legacy
```

**Verificar:** el reporte no lista problemas. Si aparece un correo vacío o
duplicado, es que alguien creó un usuario en el legacy después de las
correcciones del Paso 5.1 — se resuelve con el mismo script y se reintenta.

### 4. Validar (T+12)

```bash
python -m scripts.verificar_migracion --muestra 10
```

**Todos los conteos tienen que cuadrar.** Un descuadre sin explicación es
criterio de abortar.

```bash
python -m scripts.migrate_files --media-root /var/media-legacy --verificar
```

**Verificar:** cero faltantes, cero discrepancias.

### 5. Cambiar el routing (T+15)

```bash
sudo ln -sf /etc/nginx/sites-available/amvarmar-lms /etc/nginx/sites-enabled/amvarmar
sudo nginx -t && sudo systemctl reload nginx
```

**Verificar:** `curl -I https://app.amvarmar.com/` responde desde el sistema
nuevo.

### 6. Pruebas de humo (T+17)

Las cinco tienen que pasar. **Si alguna falla, rollback.**

```bash
# 1. Vive y sus dependencias responden
curl -fsS https://app.amvarmar.com/api/v1/health/live
curl -fsS https://app.amvarmar.com/api/v1/health/ready

# 2. Un usuario real entra con su contraseña de siempre
curl -fsS -X POST https://app.amvarmar.com/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"<cuenta real>","password":"<su contraseña>"}'

# 3. Ve sus cargas, y el número coincide con lo que decía el sistema viejo
curl -fsS https://app.amvarmar.com/api/v1/shipments?limit=5 \
  -H "Authorization: Bearer $TOKEN"

# 4. Descarga un documento real
curl -fsS https://app.amvarmar.com/api/v1/documents/<id>/download \
  -H "Authorization: Bearer $TOKEN"

# 5. Crear una carga de prueba y cancelarla
```

La prueba 2 es la que más dice: si el hash PBKDF2 del legacy no valida, nadie
puede entrar y el resto no importa.

### 7. Habilitar escrituras (T+30)

Solo después de que las cinco pasen.

```bash
sudo systemctl start amvarmar-worker    # outbox
```

**Verificar:** crear una carga desde la interfaz, moverla de estado, y que
llegue el correo a Mailpit o al relay real.

## Criterios de rollback

Se aborta si ocurre **cualquiera** de estos. No hay margen de interpretación:

| Situación | Por qué es motivo de abortar |
|---|---|
| Un conteo no cuadra y nadie sabe por qué | Faltan datos y no se sabe cuáles |
| Un usuario real no puede entrar | El sistema es inservible para todos |
| Un documento real no se puede descargar | Los archivos no llegaron |
| Un cliente ve cargas de otra empresa | Fuga de datos. Rollback inmediato, sin discusión |
| Pasaron 45 min sin pasar las pruebas de humo | Se acabó la ventana |
| `/health/ready` no responde 200 de forma estable | Una dependencia está mal |

**Quien decide es la persona nombrada arriba, no quien está ejecutando.** El que
ejecuta lleva horas metido en el problema y siempre cree que le falta poco.

## Cómo volver atrás

Toma menos de 10 minutos porque el sistema viejo nunca se tocó: solo se le
quitaron las escrituras.

```bash
# 1. Routing de vuelta al legacy
sudo ln -sf /etc/nginx/sites-available/amvarmar-legacy /etc/nginx/sites-enabled/amvarmar
sudo nginx -t && sudo systemctl reload nginx

# 2. Devolverle las escrituras.
#    El `SET` de la sesión va PRIMERO: sin él, la conexión nace en solo lectura
#    y el ALTER falla. Es el error que rompería el rollback.
sudo -u postgres psql -d amvarmar_inventory <<'SQL'
SET default_transaction_read_only = off;
ALTER DATABASE amvarmar_inventory SET default_transaction_read_only = off;
SQL
sudo systemctl restart amvarmar-legacy

# 3. Comprobar que se puede operar
curl -fsS https://app.amvarmar.com/
```

**No hace falta restaurar el respaldo**: el legacy conserva sus datos intactos,
porque durante toda la ventana estuvo en solo lectura. El respaldo del paso 2 es
el seguro por si algo salió peor de lo previsto.

La base del sistema nuevo se deja como quedó, sin borrar: sirve para investigar
qué falló antes del siguiente intento.

**Avisar a los clientes** que se sigue en el sistema de siempre.

## Después del cutover

- El sistema viejo queda **apagado pero restaurable durante 90 días**. No se
  borra nada hasta que ese plazo pase y la operación confirme que todo funciona.
- Ver [estabilizacion.md](estabilizacion.md) para las dos semanas siguientes.

## Ensayo

Este runbook se ensaya completo en staging **antes** del día, incluido un
rollback de prueba. Un procedimiento que nunca se ejecutó no es un
procedimiento: es una intención.

```bash
infra/cutover/ensayo.sh
```
