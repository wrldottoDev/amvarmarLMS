# ADR-0002: Tabla de traducción de estados legacy

- **Fecha:** 2026-08-19
- **Estado:** Aprobado
- **Aprobado por:** Otoniel González (revisión técnica) + AMVARMAR (decisión de negocio)

## Contexto

Es la decisión más delicada del proyecto. Los 4 estados legacy (`PENDIENTE`, `APROBADO`, `RECHAZADO`, `COMPLETADO`)
son ambiguos y se comparten entre `Warehouse` y `DispatchRequest` con significados distintos según la tabla.

Distribución real medida en el Paso 0.3 (`docs/migration/inventario.md`), sobre `core_warehouse.status`:

| Status | Warehouses |
|---|---|
| APROBADO | 15 |
| COMPLETADO | 541 |
| PENDIENTE | 28 |
| RECHAZADO | 0 |
| **Total** | **584** |

`RECHAZADO` no aparece en `Warehouse.status` — solo se usa en `DispatchRequest.status` (proceso de despacho).

`core_warehouse` **no tiene campo de fecha de recepción**. El único timestamp es `created_at`
(`auto_now_add`, fecha de creación del registro en el sistema, no necesariamente de recepción física).
Cualquier regla de traducción tiene que ser computable con los campos que existen — no se puede migrar
inventando una fecha que el legacy nunca guardó.

## Decisión

| Legacy `Warehouse.status` | LMS `shipments.status` | Regla |
|---|---|---|
| `PENDIENTE` | `legacy_review_required = true` en todos los casos | No hay fecha de recepción en el legacy — no se infiere `STORED` a partir de la sola presencia de documentos, porque eso no prueba recepción física. 28 registros (5% del total), se revisan a mano en Fase 5. |
| `APROBADO` | `DISPATCH_REQUESTED` | Solo si el warehouse tiene al menos una fila en `core_dispatchrequestitem`. Si no tiene ninguna solicitud de despacho vinculada, el mapeo no está respaldado por datos → `legacy_review_required = true` en su lugar. |
| `COMPLETADO` | `DISPATCHED` (no `DELIVERED` — no hay prueba de entrega) | Sin marca. 541 de 584 (93%). |
| `RECHAZADO` | No aplica a `Warehouse` (0 casos). Cuando aparezca en `DispatchRequest.status`, mantiene el estado logístico previo del shipment; el rechazo se modela en `dispatch_requests`, no en `shipments.status`. | — |

**Trazabilidad:** se agrega columna `shipments.legacy_status VARCHAR` que guarda el string crudo del
legacy (`PENDIENTE`/`APROBADO`/`COMPLETADO`/`RECHAZADO`). Se llena solo en filas migradas, queda `NULL`
en shipments creados nativos en el LMS, y **nunca se borra** — es el rastro permanente para resolver
`legacy_review_required` después del cutover. No se expone en serializers de cliente, solo en vistas internas/ops.

**Regla dura:** ningún registro dudoso se migra inventando estado. Se marca `legacy_review_required` y se resuelve manualmente después.

## Alternativas consideradas

- Inferir `PENDIENTE` → `STORED` cuando tiene documentos asociados (`EXISTS` en `core_warehousedocument`),
  usando `created_at` como proxy de fecha de recepción. Descartada: es una suposición sobre un dato que el
  legacy nunca capturó explícitamente: la presencia de un documento no prueba que la carga llegó físicamente
  al almacén, y `created_at` puede ser la fecha en que se cargó el registro por otro motivo administrativo.
- Migrar `APROBADO` a `DISPATCH_REQUESTED` sin verificar `core_dispatchrequestitem`. Descartada: dejaría
  shipments en un estado que implica una solicitud de despacho activa que en realidad no existe en los datos.

## Consecuencias

- El número de registros con `legacy_review_required = true` va a ser mayor que si se hubiera intentado
  inferir `PENDIENTE`, pero cada caso marcado tiene una razón concreta y verificable, no una suposición.
- El migrador de la Fase 5 puede implementar esta tabla de forma determinista con los campos que
  realmente existen en el legacy.
- `legacy_status` agrega una columna que vive para siempre en `shipments`; no es limpieza posterior al
  cutover, es un campo permanente de auditoría.
