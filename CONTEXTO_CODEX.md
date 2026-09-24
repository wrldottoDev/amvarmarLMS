# AMVARMAR LMS — contexto del proyecto

Documento de contexto para asistentes de código. Estado al 2026-08-24.

---

## Qué es

Sistema de gestión logística (LMS) para AMVARMAR, empresa de carga internacional.
Reemplaza un WMS legacy en Django 5.2 + DRF + PostgreSQL que sigue en producción.

**No es una migración de framework.** El punto central del proyecto es cambiar el modelo de datos:
el legacy gira alrededor de `Warehouse` (con `wr_number` como PK), el nuevo gira alrededor de
`Shipment` — el expediente de carga, con identidad estable e historial auditable.

## Stack

**Backend:** Python 3.12+ (el entorno actual usa 3.14), FastAPI async, SQLAlchemy 2.x async,
asyncpg, Alembic, PostgreSQL 16, Redis 7, Celery, S3-compatible (MinIO en local).
**Frontend (no empezado):** Next.js App Router + TypeScript.
**Móvil (fase final):** Kotlin/Compose y Swift/SwiftUI nativos.

Arquitectura: **monolito modular por dominios**, no microservicios.

## Documentos que mandan

Leer en este orden antes de escribir código:

1. `documentation/AMVARMAR_LMS_Arquitectura_Tecnica_v1.md` — arquitectura completa, esquema de
   base de datos tabla por tabla, API REST, seguridad. Es la referencia técnica base.
2. `docs/adr/00*.md` — 11 ADRs **aprobados** con las decisiones de negocio y sus consecuencias.
   Varios **modifican o extienden** el documento de arquitectura; cuando hay conflicto, **gana el ADR**
   (es posterior y está firmado por el cliente).
3. `PLAN_DE_TRABAJO.md` — plan por fases y pasos, con gates. **Está en `.gitignore`**, es interno.

## Convenciones de código

- **Todo en español**: nombres de funciones, variables, comentarios, docstrings, mensajes de error.
  Excepción: términos técnicos, nombres de tabla/columna, códigos de permiso y de estado, que van en
  inglés (`shipments.read`, `PRE_ALERT`, `company_memberships`).
- Comentarios: explican **por qué**, no qué. Si el código ya dice qué hace, el comentario sobra.
- `ruff` (lint + format, línea 100) y `mypy --strict` deben pasar. **Cero `type: ignore`** en el
  código actual; si hace falta uno, es señal de que el tipo está mal modelado.
- Nada de lazy loading implícito en async: `selectinload`/`joinedload` explícito.
- Una `AsyncSession` por request. Nunca compartida entre tareas concurrentes.

## Convenciones del esquema

Definidas en `backend/app/core/database.py`, aplican a toda tabla nueva:

- **PK UUID** con `server_default=gen_random_uuid()` (extensión `pgcrypto`) — se genera en la base,
  no en Python, para que valga también si inserta el migrador legacy por SQL directo.
- **`TIMESTAMPTZ` en todo campo de fecha/hora.** Forzado globalmente por `Base.type_annotation_map`:
  todo `Mapped[datetime]` se convierte solo. No se puede olvidar.
- **`CITEXT`** para emails y códigos (permisos, roles): la unicidad no depende de que la aplicación
  recuerde normalizar a minúsculas.
- **`NAMING_CONVENTION`** de constraints en `MetaData`. Sin esto Alembic genera nombres aleatorios
  y el `downgrade` no puede eliminar por nombre lo que creó.
- Estados como `VARCHAR(20)` + `CHECK`, **no** tipo `ENUM` de PostgreSQL (más fácil de migrar).
- `TimestampMixin` para `created_at`/`updated_at` manejados por la base.

---

## Estado actual: FASE 1 COMPLETA (pasos 1.1 a 1.8)

### Ya construido

```
backend/
├── app/
│   ├── main.py                    FastAPI, falla al arrancar si falta config
│   ├── core/
│   │   ├── config.py              Settings (pydantic-settings), todo campo sin default es obligatorio
│   │   ├── database.py            Base, engine, sessionmaker, get_session, convenciones del esquema
│   │   ├── redis.py               pool + dependencia get_redis
│   │   ├── errors.py              formato único de error + manejadores
│   │   ├── middleware.py          request_id + log de acceso
│   │   ├── logging.py             structlog JSON
│   │   ├── rate_limit.py          ventana fija en Redis
│   │   ├── idempotency.py         header Idempotency-Key
│   │   └── security/
│   │       ├── argon2.py          hash, verify, needs_rehash, rehash progresivo
│   │       ├── jwt.py             emisión y validación de claims completos
│   │       └── token_fingerprint.py  HMAC-SHA-256 con clave dedicada
│   ├── api/health.py              /health/live (no toca DB) y /health/ready (verifica DB + Redis)
│   └── modules/
│       ├── users/models.py        User
│       ├── companies/models.py    Company, CompanyMembership
│       ├── auth/                  models, service, router (9 endpoints), dependencies, schemas
│       ├── audit/                 models (audit_logs, idempotency_keys, outbox_events), redaction, service
│       └── rbac/
│           ├── models.py          Role, Permission, RolePermission, UserRoleAssignment, ScopeType, RoleCode
│           ├── catalog.py         FUENTE ÚNICA DE VERDAD: 29 permisos, 5 roles (deriva de ADR-0004)
│           ├── service.py         permisos efectivos + caché Redis + invalidación
│           └── dependencies.py    require_permission(code, resolver_empresa)
├── alembic/versions/              5 migraciones
├── scripts/seed_rbac.py           seed idempotente
└── tests/                         335 tests, todos en verde, corren también en paralelo
    ├── unit/ integration/ security/ e2e/
    └── conftest.py                testcontainers PostgreSQL 16 + Redis 7, aislamiento transaccional
```

**Migraciones aplicadas:**
1. `d62ea549f5a9` — extensiones `pgcrypto` y `citext`
2. `ebf59dd9d030` — `users`, `companies`, `company_memberships`
3. `a124aa9f2682` — `roles`, `permissions`, `role_permissions`, `user_role_assignments`
4. `2741d14a0953` — `auth_sessions`, `refresh_tokens`
5. `e822495b9f01` — `audit_logs`, `idempotency_keys`, `outbox_events`, `one_time_tokens`

**Endpoints en OpenAPI:** `POST /api/v1/auth/{login,refresh,logout,logout-all,password/forgot,password/reset}`,
`GET /api/v1/auth/sessions`, `DELETE /api/v1/auth/sessions/{id}`, `GET /api/v1/me`, `/health/{live,ready}`.

**CI:** `.github/workflows/ci.yml` con 5 jobs — calidad, pruebas, migraciones (ciclo completo + `alembic check`
+ seed idempotente ×2), seguridad (detect-secrets, pip-audit, bandit) e imagen (build + verificar que no
lleva `.env` + Trivy).

### Modelo de identidad (ADR-0011)

```
Empresa (companies)
  └── usuario cliente → company_memberships → CLIENT_ADMIN o CLIENT_USER
                         (UNIQUE(user_id): 1 usuario = 1 empresa)

Staff AMVARMAR (users) → SIN membership → rol con alcance GLOBAL
                          SUPER_ADMIN / OPS_ADMIN / OPS_AGENT
```

Regla binaria: **tenés membership = sos cliente; tenés rol GLOBAL = sos staff.**
**No existe `is_staff`** — hay un test que verifica que esa columna no exista. La autorización sale
siempre del rol asignado, nunca de un flag en el usuario.

### RBAC

5 roles: `SUPER_ADMIN`, `OPS_ADMIN`, `OPS_AGENT`, `CLIENT_ADMIN`, `CLIENT_USER`.
4 alcances: `GLOBAL`, `ORGANIZATION`, `ASSIGNED`, `OWN`.
29 permisos formato `recurso.accion`.

Puntos que no son obvios y conviene no romper:

- **El alcance vive en `user_role_assignments`, no en `roles`** (el documento de arquitectura lo pone
  en `roles`; ADR-0004 lo corrige). `roles.allowed_scopes` es un array que declara qué alcances admite
  el rol — `OPS_AGENT` puede ser `GLOBAL` o `ASSIGNED` según el puesto de cada persona.
- **La caché de permisos se invalida subiendo `users.authz_version`**, que forma parte de la clave de
  Redis (`authz:{user_id}:v{n}`). No se borran claves. Así la invalidación funciona aunque Redis esté
  caído, y no hay ventana en la que la base ya cambió pero Redis sirve permisos viejos.
- **`require_permission` devuelve `404`, no `403`.** Confirmar que un recurso existe pero es de otra
  empresa ya filtra información.
- **`user_role_assignments` tiene dos índices únicos parciales**, no uno compuesto: en PostgreSQL
  `NULL` no colisiona con `NULL`, así que un `UNIQUE(user_id, role_id, company_id)` dejaría duplicar
  asignaciones globales.
- **El seed retira permisos además de agregarlos.** Sin eso, quitar un permiso del catálogo no tendría
  efecto sobre una base ya sembrada.
- `SUPER_ADMIN` se calcula como `frozenset(PERMISSIONS)`, no se enumera: un permiso nuevo lo obtiene
  automáticamente.
- **`ASSIGNED` y `OWN` todavía no los resuelve `permite()`** — necesitan datos del recurso (a quién
  está asignado, quién lo creó) que esa capa no tiene. Se resuelven en la política de dominio del
  Paso 2.2, cuando exista `shipment_assignments`.

### Autenticación: LISTA

`app/modules/auth/dependencies.py::actor_actual` resuelve el usuario desde el access token. Puntos que
no se deben romper:

- **Cada request autenticado consulta `sesion_activa()`.** Un access token es válido criptográficamente
  hasta que expira; sin esa consulta un logout no tendría efecto durante 10 minutos.
- **Rotación de refresh sin carreras:** `UPDATE refresh_tokens SET used_at=now() WHERE id=:id AND used_at
  IS NULL RETURNING id`. Sin ese `WHERE`, dos refresh simultáneos emitirían dos pares válidos. Probado
  80 veces sin fallo intermitente.
- **Hash señuelo** en login cuando el correo no existe: sin él, la diferencia de tiempo (~85 ms vs ~0 ms)
  permite enumerar cuentas.
- **El estado de la cuenta se revisa DESPUÉS de verificar la contraseña**, o se podría descubrir qué
  cuentas están suspendidas sin conocer la clave.
- **Una sola excepción para todo fallo de login** (mala contraseña, cuenta inactiva, inexistente).
- El refresh va en cookie `HttpOnly`/`SameSite=Strict`/`Path=/api/v1/auth/refresh`, **nunca en el cuerpo**.

### Transversales (Paso 1.7)

- **Formato único de error** (`app/core/errors.py`): todo error responde
  `{"error": {code, message, details, request_id}}`. El `message` nunca lleva nombres de tabla, trazas ni
  el motivo interno de una denegación.
- **`request_id`** por request (`app/core/middleware.py`): acepta el header entrante **solo si es un UUID
  válido** — aceptar cualquier string permitiría inyectar contenido en los logs.
- **Auditoría con redacción obligatoria** (`app/modules/audit/`): `redactar()` se aplica dentro de
  `registrar()`, no en el llamador; si cada caller tuviera que acordarse, tarde o temprano uno no lo hace.
  La lista de bloqueo coincide por subcadena y cubre inglés y español.
- **Rate limiting** (`app/core/rate_limit.py`): ventana fija en Redis. Login por cuenta (5/5min) y por IP
  (30/5min, más holgado porque una oficina comparte NAT).
- **Idempotencia** (`app/core/idempotency.py`): misma clave con distinto cuerpo → `409`, no la respuesta
  anterior.

---

## Lo que sigue

| Paso | Contenido |
|---|---|
| **Fase 2** | `shipments` y su núcleo. Es lo que sigue. |
| Fase 3 | Documentos y despachos. |
| Fase 4 | Outbox, notificaciones, observabilidad. |
| Fase 5 | Migración legacy y cutover. |
| Fase F | Frontend Next.js (arranca al cerrar Fase 1). |
| Fase M | Apps móviles (después de Fase 5). |

---

## Decisiones de negocio ya cerradas (los 11 ADRs)

- **0001** — 9 estados: `PRE_ALERT → IN_TRANSIT → RECEIVED → STORED → DISPATCH_REQUESTED → PREPARING
  → DISPATCHED → DELIVERED`, más `CANCELLED` terminal. Solo se cancela desde `PRE_ALERT`/`IN_TRANSIT`.
  Retrocesos: un paso, con `OPS_ADMIN`+ y justificación obligatoria. Revertir `DELIVERED`: solo `SUPER_ADMIN`.
- **0002** — Traducción de estados legacy. `PENDIENTE` (28 filas) → todas `legacy_review_required`
  (el legacy no tiene fecha de recepción, no se infiere). `APROBADO` (15) → `DISPATCH_REQUESTED` solo
  si tiene solicitud real. `COMPLETADO` (541) → `DISPATCHED`, nunca `DELIVERED`. Campo `legacy_status`
  guarda el valor crudo para siempre.
- **0003** — 6 tipos de documento. Requisitos documentales usan 5 estados
  (`PENDING/UPLOADED/VERIFIED/REJECTED/NOT_APPLICABLE`), no los 3 genéricos del documento de
  arquitectura. `NOT_APPLICABLE` ("nunca aplicó") ≠ `WAIVED` ("se exoneró algo obligatorio").
- **0004** — RBAC. Ver arriba.
- **0005** — Catálogo `locations`/`facilities`. La regla de WR **no** compara ciudad: se ata a
  `origin_facility.uses_warehouse_receipt`. Miami además **exige** WR antes de `STORED`.
  Migración: las 584 cargas legacy son Miami por conocimiento confirmado, no por inferir de shipper.
- **0006** — `DELIVERED` exige prueba de entrega verificable (fecha, documento, receptor, actor).
  Tabla nueva `delivery_disputes` para inconformidad del cliente.
- **0007** — Retención 6 meses desde `DELIVERED`/`CANCELLED`. **No se borra nada**: se recomprime al
  máximo y se archiva. PDF firmados: solo compresión de contenedor (recomprimir invalida la firma).
  Campos `retention_until`, `archived_at`, `legal_hold`. Auditoría: 2 años aparte.
- **0008** — Canales: `IN_APP` + `EMAIL` obligatorios y no desactivables. WhatsApp fase futura.
  Push depende de la fase móvil. 8 eventos críticos. El correo **nunca** lleva adjuntos ni datos
  sensibles, solo un enlace.
- **0009** — PDF 250 MB, imágenes 100 MB, lote 1 GB, configurables por `SUPER_ADMIN` vía
  `system_settings`. **ZIP prohibido** en subida. **Escanear con antivirus ANTES de optimizar**
  (procesar contenido no confiable con Pillow/parsers de PDF es superficie de ataque).
  Formatos permitidos **por tipo de documento**, no whitelist global. DOCX se convierte a PDF.
  Tres estados independientes en `documents`: `upload_status`, `scan_status`, `requirement_status`.
- **0010** — Apps nativas separadas, después de Fase 5. A partir de ahí, versionado de API obligatorio.
- **0011** — Modelo de identidad. Ver arriba.

## Reglas de seguridad que no se negocian

- **Deny-by-default.** Sin permiso explícito, sin acceso.
- **Aislamiento por empresa.** Un cliente nunca ve datos de otra empresa, ni con UUID válido. `404`.
- **El backend valida siempre.** Ocultar un botón en el frontend no es control de seguridad.
- **Nunca se exponen** `password_hash`, fingerprints de refresh, `storage_key`, ni el motivo interno
  por el que falló una autorización.
- **Auditoría append-only.** Nadie borra `audit_logs`. Campos prohibidos ahí: contraseñas, hashes,
  JWT, refresh tokens, cookies, API keys, contenido de archivo, credenciales SMTP/FCM.
- `shipment_events` es append-only: una corrección se registra como evento nuevo, nunca `UPDATE`.
- **Secretos**: `.env` gitignorado, `.env.example` solo placeholders, `detect-secrets` en pre-commit.
- Tres claves criptográficas **separadas**: firma JWT, fingerprint de refresh, cifrado de tokens FCM.

## Errores del legacy que no hay que repetir

| Patrón viejo | Por qué falla |
|---|---|
| `wr_number` como PK | Amarra la identidad a un dato de negocio de una sola bodega |
| 4 estados compartidos entre `Warehouse` y `DispatchRequest` | Los mismos valores significan cosas distintas según la tabla |
| Correo enviado desde `Model.save()` con `transaction.on_commit` | Si el proceso muere, la notificación se pierde sin rastro → usar outbox |
| Refresh JWT de 7 días sin rotación | Un token robado sirve una semana |
| Archivos en `MEDIA_ROOT` servidos por la app | Sin control de acceso real por empresa |
| Tokens FCM en claro | Filtración de base = push a todos los clientes |
| Lógica de negocio dentro de `save()` | Imposible de probar aislada, se dispara donde no debe |

---

## Cómo correr

```bash
# Servicios (puertos remapeados: el host ya tenía Postgres 5432 y Redis 6379 ocupados)
cd infra/docker && docker compose up -d
# postgres 5434 · redis 6380 · minio 9000/9001 · mailpit 1025/8025

cd backend
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)

alembic upgrade head
python -m scripts.seed_rbac
uvicorn app.main:app --reload --port 8001   # 8000 ocupado en el host

pytest -q                    # 335 tests, usa testcontainers (no la base de desarrollo)
ruff check app/ tests/ && mypy app/ scripts/
alembic check                # falla si hay modelos sin migración
```

`DEBUG=true` activa `echo` de SQLAlchemy: la salida de los scripts sale muy verbosa. Filtrar o
poner `DEBUG=false` para leerla.
