# Prompt para Codex — Frontend AMVARMAR LMS (Fase F.1 a F.4)

Copiá todo lo que sigue como prompt.

---

## Contexto

Trabajás en `/Users/ottogonzalez/Documents/amvarmar/amvarmarLMS`. El backend está terminado hasta la
Fase 2 y corre. Tu tarea es construir el frontend en `frontend/`, que hoy está **vacío** (solo un
`.gitkeep`).

**Leé primero `CONTEXTO_CODEX.md` en la raíz del repo.** Contiene la arquitectura, las convenciones y los
11 ADR aprobados. Los ADR mandan sobre el documento de arquitectura cuando hay conflicto.

### Levantar el backend para trabajar contra él

```bash
cd infra/docker && docker compose up -d
# postgres 5434 · redis 6380 · minio 9000/9001 · mailpit 1025/8025

cd backend
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)
alembic upgrade head
python -m scripts.seed_rbac
python -m scripts.seed_shipment_statuses
python -m scripts.seed_document_types
uvicorn app.main:app --reload --port 8001
```

El OpenAPI queda en `http://localhost:8001/openapi.json`. **Generá el cliente TypeScript desde ahí**, no
escribas tipos a mano:

```bash
npx openapi-typescript http://localhost:8001/openapi.json -o src/lib/api/generated.ts
```

Ya está verificado que genera limpio (19 rutas, 27 esquemas).

---

## Stack (fijado por el plan de trabajo, no lo cambies)

- **Next.js** con App Router + **TypeScript**
- **TanStack Query** para estado de servidor
- **Zod** para validación de formularios
- Cliente API generado desde OpenAPI

Para estilos podés usar **Tailwind CSS + shadcn/ui**, salvo que el usuario indique otra cosa.

Estructura de directorios (del documento de arquitectura, sección 7):

```
frontend/src/
├── app/
│   ├── (auth)/          login, recuperar contraseña
│   ├── (client)/        portal del cliente
│   ├── (admin)/         operaciones
│   └── layout.tsx
├── components/
│   ├── ui/
│   ├── shipments/
│   └── dashboard/
├── features/
│   ├── auth/
│   └── shipments/
├── lib/
│   ├── api/
│   │   ├── client.ts
│   │   ├── generated.ts       ← generado, no editar a mano
│   │   └── refresh-mutex.ts
│   ├── auth/
│   ├── query-client.ts
│   └── validation/
├── hooks/
├── styles/
└── types/
```

---

## Endpoints disponibles (los únicos que existen hoy)

```
POST   /api/v1/auth/login
POST   /api/v1/auth/refresh
POST   /api/v1/auth/logout
POST   /api/v1/auth/logout-all
GET    /api/v1/auth/sessions
DELETE /api/v1/auth/sessions/{session_id}
POST   /api/v1/auth/password/forgot
POST   /api/v1/auth/password/reset
GET    /api/v1/me

GET    /api/v1/shipments                                    listado con cursor
GET    /api/v1/shipments/{id}                               detalle
GET    /api/v1/shipments/{id}/timeline                      línea de tiempo
POST   /api/v1/shipments/{id}/transitions                   cambiar estado
POST   /api/v1/shipments/{id}/requirements                  abrir requisito
PATCH  /api/v1/shipments/{id}/requirements/{requirement_id} resolver requisito

GET    /api/v1/dashboard/client
GET    /api/v1/dashboard/operations
```

**NO existen todavía** (Fase 3+): subir documentos, crear cargas (`POST /shipments`), editar cargas
(`PATCH /shipments/{id}`), despachos, notificaciones, gestión de usuarios. No construyas pantallas
contra endpoints inexistentes.

---

## Reglas que NO se pueden romper

### 1. El frontend nunca autoriza

`GET /me` devuelve `permisos: string[]`. Se usa **solo para adaptar la interfaz** — ocultar un botón,
no mostrar una sección. El backend vuelve a verificar cada request. Que el botón esté oculto no es
seguridad; que la API responda 404 sí lo es.

### 2. Mutex de refresh — crítico

El backend usa refresh rotatorio con **detección de reutilización**: presentar un refresh ya usado
**revoca la sesión completa**.

Si varias peticiones reciben `401` a la vez y cada una dispara `/auth/refresh`, el backend interpreta la
segunda como reutilización y **cierra la sesión del usuario**. Implementá `lib/api/refresh-mutex.ts`: solo
una petición dispara el refresh, las demás esperan su resultado y reintentan con el token nuevo.

Esto no es una optimización. Sin el mutex, un usuario con dos pestañas abiertas se desloguea solo.

### 3. La cookie de refresh no la maneja JavaScript

El refresh viaja en cookie `HttpOnly` / `SameSite=Strict` / `Path=/api/v1/auth/refresh`. **No intentes
leerla ni guardarla.** Solo llamá a `POST /api/v1/auth/refresh` con `credentials: "include"` y el navegador
la manda solo. El `access_token` sí viene en el cuerpo y va en el header `Authorization: Bearer`.

Guardá el access token **en memoria**, no en `localStorage` — ahí queda expuesto a XSS.

### 4. Estado y pendientes son ejes separados

**El concepto central del proyecto.** Una carga puede estar `IN_TRANSIT` y tener documentos faltantes al
mismo tiempo. "Faltan documentos" **NO es un estado** — nunca reemplaces `IN_TRANSIT` con "Faltan
documentos" en la interfaz.

La API lo refleja con campos separados:

```ts
{
  status: "IN_TRANSIT",              // el estado logístico, siempre visible
  open_requirements_count: 2,        // total pendiente
  client_action_required_count: 1,   // lo que le toca al CLIENTE
}
```

Mostrá el estado como estado, y lo pendiente como **badge adicional**. Para el cliente usá
`client_action_required_count`: un packing list pendiente es de Operaciones y no debe verse como acción
suya.

### 5. Paginación por cursor, no por página

`GET /shipments` devuelve `{ items, next_cursor, has_more }`. El cursor es **opaco** — no lo parsees ni
construyas uno. No hay números de página ni total de resultados. Usá scroll infinito o un botón "cargar
más" con `useInfiniteQuery` de TanStack Query.

### 6. Formato único de error

Todo error de la API responde así:

```json
{
  "error": {
    "code": "SHIPMENT_TRANSITION_INVALID",
    "message": "No se puede pasar de STORED a IN_TRANSIT",
    "details": [{ "from": "STORED", "allowed": ["DISPATCH_REQUESTED"] }],
    "request_id": "018f..."
  }
}
```

Ramificá sobre `code` (es contrato estable), mostrá `message` al usuario (viene en español, listo para
mostrar). Mostrá el `request_id` en errores 500 — es lo que permite correlacionar con los logs del
servidor.

Códigos que vas a encontrar: `NO_AUTENTICADO` (401), `RECURSO_NO_ENCONTRADO` (404),
`SHIPMENT_TRANSITION_INVALID` (409), `SHIPMENT_VERSION_CONFLICT` (409),
`SHIPMENT_REQUIREMENTS_PENDING` (409), `TRANSICION_EXIGE_MOTIVO` (422), `PAYLOAD_INVALIDO` (422),
`DEMASIADAS_SOLICITUDES` (429, con header `Retry-After`), `CURSOR_INVALIDO` (400).

### 7. `row_version` en toda mutación

`GET /shipments/{id}` devuelve `row_version`. Toda transición lo exige en el body. Si no coincide,
responde `409 SHIPMENT_VERSION_CONFLICT` con el valor actual en `details`.

Ante ese 409: recargá la carga y avisale al usuario que alguien más la modificó. No reintentes
automáticamente con el valor nuevo — perderías el cambio de la otra persona sin que nadie se entere.

### 8. Idioma

Toda la interfaz en **español**. El código (nombres de variables, funciones, comentarios) también en
español, salvo términos técnicos y nombres de la API (`shipment_number`, `PRE_ALERT`, `useQuery`).

---

## Qué construir

### F.1 — Proyecto y cliente API

- Proyecto Next.js (App Router, TypeScript, Tailwind).
- Generar `src/lib/api/generated.ts` desde el OpenAPI del backend.
- `src/lib/api/client.ts`: wrapper con `baseUrl` configurable, header `Authorization`,
  `credentials: "include"`, y manejo del formato único de error.
- `src/lib/query-client.ts`: TanStack Query configurado.
- Layout base y sistema de diseño mínimo.

**Listo cuando:** el proyecto compila, `npm run build` pasa, y el cliente TS refleja los 19 endpoints.

### F.2 — Autenticación

- Pantalla de login (`app/(auth)/login`).
- `src/lib/api/refresh-mutex.ts` con la lógica de mutex descrita arriba.
- Access token en memoria; refresh automático transparente al recibir 401.
- Contexto/hook de sesión que expone usuario, empresa y permisos desde `GET /me`.
- Rutas protegidas: sin sesión, redirigir a login.
- Logout y "cerrar todas las sesiones".
- Pantalla de sesiones activas (`GET /auth/sessions`, `DELETE /auth/sessions/{id}`) marcando cuál es la
  actual (`es_sesion_actual`).
- Recuperación de contraseña: `password/forgot` responde siempre lo mismo exista o no la cuenta — la
  interfaz debe reflejar eso, sin decir "ese correo no existe".

**Listo cuando:** se puede entrar, el token se refresca solo, dos pestañas abiertas no se desloguean
entre sí, y el logout invalida el acceso de inmediato.

### F.3 — Listado y detalle de cargas

- Listado (`app/(client)/shipments`) con `useInfiniteQuery` y cursor.
- Filtros: estado (multi), rango de ETA, búsqueda por texto (`q` busca en `shipment_number` y en
  cualquier referencia, incluida la factura).
- Cada fila muestra: `shipment_number`, factura (`invoice`, puede ser `null` — mostrá el
  `shipment_number` como alternativa), origen y destino (`{ location_code, name, country_code }`), ETA,
  **estado** y **badge de pendientes** por separado.
- Detalle (`app/(client)/shipments/[id]`) con los datos completos y las fechas de hito (`received_at`,
  `stored_at`, `dispatched_at`, `delivered_at`).
- Línea de tiempo (`GET /shipments/{id}/timeline`), también paginada por cursor. Cada evento trae
  `occurred_at` (cuándo pasó) y `recorded_at` (cuándo se cargó) — si difieren, indicá que es un registro
  atrasado.
- Acción de cambiar estado (`POST /transitions`) para quien tenga el permiso. Los retrocesos, cancelación
  y reapertura **exigen justificación**: si el usuario no la escribe, la API responde
  `TRANSICION_EXIGE_MOTIVO`. Pedila en el formulario antes de enviar.
- Antes de `PREPARING → DISPATCHED`, mostrá un diálogo de confirmación: "¿Está seguro de que desea
  despachar esta carga?" (ADR-0001, es requisito de interfaz, no del backend).

**Listo cuando:** se recorre el listado completo sin repetir ni omitir filas, el detalle carga, y una
transición inválida muestra el mensaje del backend con las transiciones permitidas.

### F.4 — Dashboard

- `app/(client)/dashboard` consumiendo `GET /api/v1/dashboard/client`.
- Las 5 tarjetas: `en_bodega`, `en_transito`, `proximos_a_llegar`, `requieren_accion`,
  `entregados_este_mes`.
- Sección "Próximos movimientos" con `proximos_movimientos`: factura (o alternativa si es `null`), ciudad
  y país, ETA, **estado logístico**, e **indicador separado** de pendientes.
- Vista de operaciones (`GET /api/v1/dashboard/operations`) para usuarios con alcance global.

**Listo cuando:** el dashboard responde y **ninguna tarjeta ni fila reemplaza el estado logístico con
"faltan documentos"**.

---

## Estados de carga (ADR-0001)

```
PRE_ALERT → IN_TRANSIT → RECEIVED → STORED → DISPATCH_REQUESTED
          → PREPARING → DISPATCHED → DELIVERED
```

Más `CANCELLED` (terminal). Etiquetas en español que devuelve el backend: Prealerta, En tránsito,
Recibida, Almacenada, Despacho solicitado, En preparación, Despachada, Entregada, Cancelada.

## Roles (ADR-0004)

`SUPER_ADMIN`, `OPS_ADMIN`, `OPS_AGENT` (staff, alcance global) · `CLIENT_ADMIN`, `CLIENT_USER` (cliente,
alcance de su empresa).

En `GET /me`, un usuario **con** `empresa` es cliente; **sin** `empresa` es staff de AMVARMAR. No hay
campo `is_staff`.

---

## Cómo probar

Creá datos de prueba con el backend corriendo. Necesitás al menos un usuario con contraseña conocida y
rol asignado — mirá `backend/tests/integration/test_shipments_http.py`, el fixture `entorno` arma
exactamente eso y podés replicar sus INSERT.

## Antes de dar por terminado cada paso

```bash
cd frontend
npm run build      # debe pasar
npx tsc --noEmit   # sin errores de tipos
```

No uses `any` para esquivar un tipo del cliente generado: si el tipo no calza, el problema está en cómo
se está consumiendo la API.

## Qué NO hagas

- No inventes endpoints ni campos que no estén en el OpenAPI.
- No guardes el access token en `localStorage` ni `sessionStorage`.
- No leas ni escribas la cookie de refresh desde JavaScript.
- No implementes lógica de autorización que decida si una operación procede — solo mostrar u ocultar.
- No reemplaces el estado logístico con el indicador de pendientes en ninguna vista.
- No construyas pantallas de documentos, despachos ni creación de cargas: esos endpoints no existen aún.
