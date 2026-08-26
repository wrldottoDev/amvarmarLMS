# Cómo levantar todo y ver los datos

## 1. Entorno local con datos de demostración

Sirve para trastear la interfaz sin depender del sistema viejo. Los datos son
inventados pero cubren todos los estados, así que ninguna pantalla se ve vacía.

```bash
# Infraestructura
cd infra/docker && docker compose up -d && cd ../..

# Base al día y datos de demo
cd backend
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)
alembic upgrade head
python -m scripts.seed_demo          # --limpiar para empezar de cero

# API
uvicorn app.main:app --host 127.0.0.1 --port 8001

# Interfaz, en otra terminal
cd frontend && npm run dev
```

Entrar en <http://localhost:3000>. Todas las cuentas usan la misma contraseña,
`Demo-AMVARMAR-2026`, y el script la imprime al terminar:

| Cuenta | Rol | Qué ve |
|---|---|---|
| `admin@demo.amvarmar.com` | `SUPER_ADMIN` | Todo |
| `operaciones@demo.amvarmar.com` | `OPS_ADMIN` | Todo lo operativo, incluidas correcciones |
| `agente@demo.amvarmar.com` | `OPS_AGENT` | Operación diaria, sin retrocesos ni exoneraciones |
| `cliente@demo.amvarmar.com` | `CLIENT_ADMIN` | Solo Importaciones Alfa |
| `cliente2@demo.amvarmar.com` | `CLIENT_USER` | Solo Importaciones Alfa, sin gestión |
| `beta@demo.amvarmar.com` | `CLIENT_ADMIN` | Solo Comercial Beta |

Entrar con `cliente@` y con `beta@` es la forma rápida de comprobar el
aislamiento entre empresas: cada uno ve solo lo suyo.

**El script se niega a correr si `ENVIRONMENT` no es `local`.** Crea cuentas con
una contraseña escrita en el propio archivo.

## 2. Traer la base del sistema viejo

Para mirar los datos anteriores y decidir las correcciones del Paso 5.1. Queda
en una base **separada**, `amvarmar_legacy`: son esquemas distintos y mezclarlos
haría imposible saber qué dato vino de dónde.

```bash
VPS_HOST=usuario@ip infra/backup/traer_legacy.sh
```

El volcado se hace en la VPS y viaja por la tubería de ssh, así que no hace
falta abrir el puerto de PostgreSQL a internet ni dejar el archivo en el
servidor.

Después, el diagnóstico:

```bash
docker cp docs/migration/diagnostico_5_1.sql amvarmar-lms-postgres-1:/tmp/
docker exec -i amvarmar-lms-postgres-1 \
  psql -U amvarmar -d amvarmar_legacy -f /tmp/diagnostico_5_1.sql
```

Eso lista los nueve usuarios con problemas de correo, las cargas sin cliente y
los WR mal capturados, con el contexto para decidir uno por uno.

**El volcado local trae datos reales de clientes.** Borralo cuando termines.

## 3. Ver los datos viejos DENTRO del sistema nuevo

Esto todavía no se puede: hace falta el migrador, que es el **Paso 5.2** y aún
no está construido. El orden es:

1. **5.1** — corregir en el sistema viejo lo que impide migrar (nueve usuarios
   con correos vacíos o duplicados, diecisiete cargas sin cliente). Los scripts
   están en `docs/migration/correcciones/` y probados; falta tomar las
   decisiones y ejecutarlos.
2. **5.2** — el migrador con `legacy_id_map` y modo `--dry-run`, que traduce
   `core_warehouse` a `shipments` y todo lo demás.
3. **5.3** — subir los archivos de `media/` al storage privado. **Ya está
   construido**, ver abajo. Son casi 10 GB: es la etapa larga en tiempo de
   ejecución.
4. **5.4** — ensayo completo sobre una copia, cronometrado, para saber cuánto
   dura la ventana de cutover.

Saltarse el 5.1 no ahorra tiempo: el migrador fallaría en el primer usuario con
correo duplicado, y parchearlo para tolerarlo lo volvería imposible de volver a
correr.

## Qué muestra hoy la interfaz

Está construida hasta la Fase 2. Funciona el inicio de sesión, la recuperación
de contraseña, el tablero, el listado y detalle de cargas con su línea de
tiempo, y las sesiones activas.

**Falta interfaz** para lo de Fase 3 y 4, aunque la API ya lo tiene: despachos,
subida y descarga de documentos, requisitos documentales y bandeja de
notificaciones.

## Paso 5.3 — subir los archivos del sistema viejo

El migrador de datos registró cada documento con la ruta que tenía en el
servidor viejo y lo dejó pendiente: la fila existe, el archivo no. Por eso hoy
la interfaz los muestra como "No disponible todavía".

```bash
# 1. Traer media/ desde la VPS. Se puede cortar y reanudar: son ~10 GB.
VPS_HOST=usuario@ip infra/backup/traer_media.sh

# 2. Simular. No escribe nada y lista lo que falta.
cd backend
python -m scripts.migrate_files --media-root ../media-legacy --dry-run

# 3. Subir. Commitea documento por documento, así que interrumpirlo no
#    pierde el avance y volver a lanzarlo continúa donde quedó.
python -m scripts.migrate_files --media-root ../media-legacy

# 4. Verificar uno por uno: relee del storage y recalcula el hash.
python -m scripts.migrate_files --media-root ../media-legacy --verificar
```

**El gate del paso es cero faltantes y cero discrepancias sin explicar.** El
script termina con código 1 si queda alguna, así que sirve en un pipeline.

Dos cosas que hace a propósito:

- **No borra el original.** El servidor viejo sigue siendo la copia de
  referencia hasta que el cutover termine.

Cuando el storage esté verificado, borrá `media-legacy/`: son documentos de
clientes.
