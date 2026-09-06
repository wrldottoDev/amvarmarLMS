# Implementación y verificación — cierre de la reescritura operativa

- Fecha: 2026-09-02
- Alcance: verificación final de la reescritura FastAPI + Next.js descrita en
  `04-reglas-negocio-objetivo.md`, `02-arquitectura-backend-fastapi.md`,
  `03-propuesta-frontend-nextjs.md` y ADR-0016.
- La autenticación (login, tokens, sesiones, recuperación, política administrativa de
  contraseñas) quedó fuera de alcance y no se tocó.

## 1. Qué se corrigió en esta pasada

El código ya traía implementado casi todo lo listado en `04-reglas-negocio-objetivo.md`
(piezas mínimas, conversión kg/lb canónica, WR único por facility, motor de transiciones
auditable, seguridad documental por `provided_by`/`issued_by`, `VERIFIED` exige documento
`READY`, aislamiento de documentos de despacho, SHA-256 y ZIP por streams vía Celery, flujo
de despacho completo, frontend de cargas/piezas/pesos/archivos/despachos/exports, migrador
legacy, seed demo, Playwright). Esta pasada verificó eso contra el código y corrigió lo que
quedaba pendiente o roto:

1. **`.gitignore` de `backend/app/infrastructure/storage/`** — ya confirmado corregido antes de
   empezar esta verificación: la excepción explícita (`!backend/app/infrastructure/storage/` y
   `/**`) existe y `git status` muestra `s3.py`/`__init__.py` como código versionable, no
   ignorado.
2. **`alembic check` fallaba en `head`** — 4 índices `gin_trgm_ops` (`ix_shipments_numero_trgm`,
   `ix_shipments_shipper_trgm`, `ix_shipments_carrier_trgm`,
   `ix_shipment_references_value_trgm`) existían en la migración `4c2a6e91d7b8` pero no estaban
   declarados en los modelos SQLAlchemy de `shipments`/`shipment_references`; autogenerate los
   quería borrar. Se agregaron los `Index(...)` correspondientes a
   `backend/app/modules/shipments/models.py`. `alembic check` ahora responde
   "No new upgrade operations detected."
3. **Bug real en administración de usuarios** — `ActualizarUsuarioRequest.status` en
   `admin/router.py` aceptaba `DISABLED`, valor que nunca estuvo en el `CHECK` de
   `users.status` (`INVITED`, `ACTIVE`, `SUSPENDED`); una actualización con ese valor rompía
   contra la base con un error de integridad no controlado. Corregido: el patrón del schema es
   ahora `^(ACTIVE|SUSPENDED)$` y `admin/service.py` ya no referencia `DISABLED`.
4. **Aislamiento de pruebas de integración** — 10 pruebas fallaban por orden de ejecución sobre
   el contenedor Postgres compartido de la corrida (`postgres_container` es de toda la suite;
   varias pruebas commitean filas reales vía `cliente`/`db_directa`):
   - `test_outbox.py` (7 pruebas): un fixture `autouse` vacía `outbox_events` antes de cada
     prueba del archivo, porque `reclamar()` toma el lote de 50 eventos más viejos de toda la
     corrida y el evento recién publicado por la prueba quedaba fuera del lote.
   - `test_transiciones.py` (2 pruebas): llamaban directamente a `resolver_requisito()` con
     `VERIFIED`/`REJECTED` sobre un requisito `DOCUMENT`, una vía que el código ya no expone
     para ese tipo — el servicio exige `verificar_requisito_documental()` /
     `rechazar_requisito_documental()` con `document_id`. Las pruebas se reescribieron para usar
     la API vigente; no se relajó ninguna aserción.
   - `test_correcciones_legacy.py` (1 prueba): `verificar_migracion.comparar()` hace
     `COUNT(*)` global sobre `users`/`companies`; se agregó un `TRUNCATE ... CASCADE` al inicio
     de esa prueba para partir de una base limpia.
5. **Documentación desactualizada** — `docs/GUIA_CODIGO.md`, `docs/migration/COMO_EMPEZAR.md` y
   `docs/reescritura/01-auditoria-backend-legacy-vs-actual.md` afirmaban cosas que ya no son
   ciertas (storage ignorado por git, migrador inexistente, sin interfaz admin, rutas de correo
   rotas, interfaz limitada a Fase 2). Corregidas.

Nada de esto tocó autenticación, tokens, sesiones, recuperación de contraseña ni la política
administrativa de contraseñas.

## 2. Resultado de las verificaciones

### Backend — `backend/`

| Verificación | Resultado |
| --- | --- |
| `DEBUG=false ./.venv/bin/ruff check app scripts tests` | **PASS** — sin hallazgos |
| `./.venv/bin/ruff format --check app scripts tests` | **PASS** — 138 archivos ya formateados |
| `DEBUG=false ./.venv/bin/mypy app scripts` | **PASS** — 0 errores, 98 archivos fuente |
| `DEBUG=false ./.venv/bin/pytest -q` | **928 passed, 1 skipped, 0 failed** (103.94 s) |

La única omitida es `test_objetivos_no_funcionales.py` (requiere `DATABASE_URL` externa fuera
de este entorno local); es la misma omisión histórica documentada en la auditoría anterior.

### Ciclo de migraciones Alembic

Ejecutado sobre una base temporal desechable (`amvarmar_lms_alembic_check`), nunca sobre la
base local con datos:

1. `alembic upgrade head` desde vacío — aplicó las 22 revisiones sin error, hasta `d8e4f7a12c30`.
2. `alembic downgrade f9329a7c5d0f` — revirtió 4 revisiones (`d8e4f7a12c30` → `f9329a7c5d0f`)
   sin error.
3. `alembic upgrade head` de nuevo — reaplicó las mismas 4 revisiones sin error.
4. `alembic check` — falló inicialmente (ver punto 2 de la sección 1), corregido, y en la
   segunda corrida respondió "No new upgrade operations detected."
5. Base temporal eliminada (`DROP DATABASE amvarmar_lms_alembic_check`).

### Frontend — `frontend/`

| Verificación | Resultado |
| --- | --- |
| `npm run lint` | **PASS** — 0 issues |
| `npm run types` (`tsc --noEmit`) | **PASS** — 0 errores |
| `npm test` (Vitest) | **PASS** — 4 test files, **20 passed** |
| `npm run test:e2e` (Playwright) | **PASS** — desktop-chromium: 1 passed / 1 skipped por diseño (`test.skip` cuando el spec es del otro proyecto); mobile-chromium: 1 passed / 1 skipped por diseño. 0 fallidas. |
| `npm run build` (`next build`) | **PASS** — build de producción compilado, 22 rutas generadas |

No fue necesario modificar ningún archivo frontend: los cinco checks pasaron limpios.

### OpenAPI / cliente TypeScript

`OPENAPI_URL=http://127.0.0.1:8001/openapi.json npm run api:generate` regeneró
`frontend/src/lib/api/generated.ts` contra el backend corriendo. El archivo resultante es
idéntico al que ya estaba en el árbol de trabajo: el cliente generado ya estaba sincronizado
con el contrato actual del backend.

### Higiene de git

- `git diff --check`: sin errores de espacios en blanco.
- `git status --short`: solo archivos modificados/nuevos esperados de esta reescritura; sin
  archivos huérfanos ni artefactos accidentales.
- No se ejecutó ningún `commit`, `push`, `reset` ni `checkout` destructivo durante esta
  verificación.

## 3. URLs locales al cierre

- Backend: `http://127.0.0.1:8001` (`uvicorn`, `DEBUG=false`) — `/health/ready` responde OK
  (Postgres y Redis).
- Frontend: `http://127.0.0.1:3000` sirviendo el build de producción (`next start`), no el
  servidor de desarrollo.

## 4. Riesgos y pendientes fuera de esta corrección

Documentados también en `docs/GUIA_CODIGO.md` sección 12:

- **Política de contraseñas de administración.** `admin/service.py` sigue devolviendo una
  contraseña temporal en la respuesta de alta de usuario, y `password_forgot` no publica
  todavía el evento que dispara el correo de recuperación. Ambos son parte de la
  autenticación/política de contraseñas, explícitamente fuera de alcance de esta reescritura.
- **`rbac/dependencies.py` obsoleto.** `_user_id_actual()` siempre responde 401; no lo usan los
  routers activos (usan `auth.dependencies.actor_actual`), pero no debe reutilizarse sin
  actualizarlo primero. Es código de autenticación, no se tocó.
- **Los hooks de acciones de despacho no envían `row_version`.** El backend lo acepta opcional
  y sigue siendo la fuente de verdad, pero el frontend no aprovecha el bloqueo optimista en
  esas acciones. Mejora de UX pendiente, no una corrección de datos.
- **Observabilidad asume topología externa** (`backend:8000` y exporters no definidos en este
  compose). Pendiente de completarse en el despliegue real, fuera del alcance de este cutover.
- **Paso 5.1 del runbook de migración** (`docs/migration/COMO_EMPEZAR.md`) — limpieza de datos
  legacy (correos duplicados, cargas sin cliente) sigue pendiente de que Operaciones tome las
  decisiones; los scripts ya están listos y probados.
