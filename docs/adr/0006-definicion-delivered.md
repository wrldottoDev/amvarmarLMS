# ADR-0006: Qué dispara el estado DELIVERED

- **Fecha:** 2026-08-19
- **Estado:** Aprobado
- **Aprobado por:** Otoniel González (revisión técnica) + AMVARMAR (decisión de negocio)

## Contexto

`DISPATCHED` (salió) y `DELIVERED` (llegó a destino final) no son lo mismo, pero el legacy no distingue entre
ambos — `COMPLETADO` solo prueba que el despacho se cerró, no que el cliente recibió la carga. Hay que definir
qué evento concreto marca `DELIVERED` y quién lo registra (¿confirmación del cliente? ¿del transportista?
¿carga manual del operador?).

## Decisión

Una carga es `DELIVERED` únicamente con evidencia verificable de entrega — nunca por confirmación implícita ni
por el simple paso del tiempo.

**Transición permitida:** solo `DISPATCHED → DELIVERED`. Ningún otro origen.

**Campos obligatorios para registrar la entrega** (payload específico de esta transición, más estricto que el
payload genérico de `POST /shipments/{id}/transitions` del Apéndice C del plan):

```json
POST /api/v1/shipments/{id}/transitions
{
  "to_status": "DELIVERED",
  "occurred_at": "2026-09-10T15:40:00Z",
  "row_version": 7,
  "delivery": {
    "received_by": "Juan Pérez, Bodega Central S.A.",
    "proof_document_id": "018f..."
  }
}
```

- `occurred_at` — fecha y hora de entrega. Ya era parte del esquema genérico, aquí pasa de recomendado a
  obligatorio.
- `delivery.proof_document_id` — referencia a un documento ya subido (flujo de dos tiempos, Paso 3.1) del tipo
  nuevo `PROOF_OF_DELIVERY` (ver actualización a ADR-0003 abajo).
- `delivery.received_by` — texto: persona o entidad que recibió.
- Usuario de Operaciones que registra: no viaja en el payload, se toma del actor autenticado (`sub` del JWT) —
  ya queda en `audit_logs` y en el evento de `shipment_events` sin necesidad de que el cliente lo envíe.
- Sin estos campos, `422` — la transición se rechaza igual que cualquier validación de dominio incompleta.

**Tipos de prueba aceptados** (validación de contenido del documento, no del payload): documento firmado,
fotografía de entrega, comprobante del transportista, confirmación electrónica válida. Se validan con las
mismas reglas de upload de Paso 3.1 (MIME real, tamaño máximo — el tamaño específico para `PROOF_OF_DELIVERY`
queda pendiente de ADR-0009).

**Permisos:**
- Registran entrega: `OPS_AGENT`, `OPS_ADMIN`, `SUPER_ADMIN` — ya cubierto por el permiso
  `shipments.transition.forward` de ADR-0004 (esa fila ya incluía explícitamente "incluida entrega
  (`→DELIVERED`)"), sin permiso nuevo que crear.
- Clientes consultan la prueba de entrega — mismo control de acceso que cualquier documento del expediente
  (aislamiento por empresa, Paso 3.1).
- Un cliente **no puede** marcar su propia carga como `DELIVERED` — ya excluido en ADR-0004 (`CLIENT_ADMIN` y
  `CLIENT_USER` = No en `shipments.transition.forward`).

**Inconformidad del cliente — funcionalidad nueva, sin modelo de datos previo:**

El cliente puede reportar que no reconoce la entrega, **sin que eso cambie el estado por sí solo**. Se modela
como tabla nueva:

`delivery_disputes`:

| Campo | Descripción |
|---|---|
| `id` | UUID |
| `shipment_id` | FK a `shipments` |
| `raised_by_user_id` | Cliente que reporta |
| `reason` | Texto obligatorio |
| `status` | `OPEN`, `RESOLVED_CONFIRMED` (se sostiene la entrega), `RESOLVED_REVERTED` (se revierte `DELIVERED`) |
| `created_at`, `resolved_at`, `resolved_by_user_id` | Trazabilidad |

Endpoints nuevos, no estaban en la sección 5 (API REST) del documento de arquitectura:
- `POST /shipments/{id}/delivery-disputes` — cliente reporta inconformidad.
- `PATCH /shipments/{id}/delivery-disputes/{dispute_id}` — Operaciones resuelve (`RESOLVED_CONFIRMED` o
  `RESOLVED_REVERTED`; esto último dispara la reversión de `DELIVERED`, sujeta a las mismas reglas de abajo).

**Reglas de seguridad:**
- Toda prueba de entrega queda vinculada permanentemente al expediente — nunca se desvincula ni se borra,
  ni siquiera si `DELIVERED` se revierte después.
- Revertir `DELIVERED` requiere `SUPER_ADMIN` exclusivamente, motivo obligatorio, registro de auditoría — ya
  definido en ADR-0001 y como permiso `shipments.transition.revert_delivered` en ADR-0004. Esta decisión no
  cambia esa regla, la reafirma.
- Al completarse la entrega: notificación al cliente + evento en `shipment_events` (timeline). El tipo de
  evento/notificación concreto se termina de definir en ADR-0008 (canales de notificación).

## Actualización a ADR-0003 (catálogo de documentos)

Se agrega sexto tipo de documento:

| Documento | Quién lo proporciona | Aplicabilidad | Momento límite |
|---|---|---|---|
| Prueba de entrega (`PROOF_OF_DELIVERY`) | Operaciones | Obligatoria para completar la transición `DISPATCHED → DELIVERED` | En el momento de registrar la entrega |

Se cierra el punto "Pendiente de precisar #3" de ADR-0003 (BL y `DELIVERED`): **el BL no es requisito para
`DELIVERED`** — los únicos requisitos son los cuatro de esta decisión (fecha/hora, prueba, receptor, actor).
El BL sigue siendo un documento post-`DISPATCHED` sin relación de bloqueo con la entrega.

## Alternativas consideradas

- Marcar `DELIVERED` por confirmación del cliente (que el cliente reciba y confirme en el portal). Descartada:
  depende de una acción del cliente que puede no ocurrir nunca, dejando cargas entregadas físicamente atascadas
  en `DISPATCHED` de forma indefinida — y contradice la regla de que el cliente no puede marcar su propia carga.
- No exigir prueba estructurada, solo el cambio de estado con nota libre. Descartada: sin evidencia verificable,
  `DELIVERED` sería una afirmación sin respaldo, débil ante cualquier disputa comercial o legal.

## Consecuencias

- Se agrega `delivery_disputes` al modelo de datos de Fase 2/3 — no estaba en el documento de arquitectura
  original ni en el plan de trabajo. Amplía el alcance de Paso 2.3/2.4.
- Se agregan 2 endpoints nuevos a la API REST (sección 5 del documento de arquitectura / Apéndice C del plan).
- El esquema de payload de `POST /shipments/{id}/transitions` no es uniforme para todas las transiciones:
  `DISPATCHED → DELIVERED` exige un bloque `delivery` adicional. La capa de validación de Paso 2.4 necesita
  un esquema Pydantic específico por tipo de transición sensible (ya viene de ADR-0001 para backward/cancel/
  reopen; esta decisión agrega un cuarto caso).
- ADR-0003 queda actualizado con el sexto tipo de documento y su punto pendiente #3 resuelto.
- Notificación de entrega completada queda anotada como entrada obligatoria a definir en ADR-0008.
