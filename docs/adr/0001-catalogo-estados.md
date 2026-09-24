# ADR-0001: Catálogo de estados de Shipment

- **Fecha:** 2026-08-19
- **Estado:** Aprobado
- **Aprobado por:** Otoniel González (revisión técnica) + AMVARMAR (decisión de negocio)

## Contexto

El documento de arquitectura propone la secuencia:
`PRE_ALERT → IN_TRANSIT → RECEIVED → STORED → DISPATCH_REQUESTED → PREPARING → DISPATCHED → DELIVERED`

Hay que confirmar si esta secuencia es completa para la operación real de AMVARMAR, y definir:
- Qué transiciones hacia atrás existen (ej. de `DISPATCH_REQUESTED` volver a `STORED` si se cancela la solicitud).
- Quién autoriza cada transición hacia atrás (permiso requerido).
- Si hace falta un estado adicional que el documento no contempla.

## Decisión

**Catálogo (9 estados, 8 forward + 1 terminal):**

`PRE_ALERT → IN_TRANSIT → RECEIVED → STORED → DISPATCH_REQUESTED → PREPARING → DISPATCHED → DELIVERED`

Estado terminal adicional: `CANCELLED`.

**Reglas de `CANCELLED`:**
- Solo para cargas registradas por error, duplicadas o que no continuarán el proceso.
- Solo se puede cancelar desde `PRE_ALERT` o `IN_TRANSIT`. Desde `RECEIVED` en adelante la carga ya existe
  físicamente y no se cancela — si se cancela el despacho, se modifica `dispatch_requests`, no `shipments.status`.
- Reabrir una carga `CANCELLED` requiere `OPS_ADMIN` o `SUPER_ADMIN` + justificación obligatoria. Vuelve al
  estado exacto desde el que fue cancelada (`PRE_ALERT` o `IN_TRANSIT`) — **asunción a confirmar**, ver
  "Pendiente de precisar".

**Matriz de permisos para cancelar:**

| Actor | Puede cancelar desde |
|---|---|
| `CLIENT_ADMIN` | `PRE_ALERT`, solo cargas de su propia empresa |
| `OPS_AGENT` | `PRE_ALERT` únicamente (confirmado en ADR-0004) |
| `OPS_ADMIN` / `SUPER_ADMIN` | `PRE_ALERT` o `IN_TRANSIT` |

**Cambios hacia atrás (cualquier transición backward que no sea cancelar/reabrir):**
- Requiere `OPS_ADMIN` o `SUPER_ADMIN`.
- Justificación obligatoria (texto libre, no opcional).
- Queda auditado: usuario, fecha, estado anterior, estado nuevo, motivo. Se escribe como evento nuevo en
  `shipment_events` (append-only) — nunca se sobreescribe ni se borra historial.
- Revertir `DELIVERED` es el caso más sensible: requiere **exclusivamente** `SUPER_ADMIN` (no `OPS_ADMIN`),
  como excepción más estricta dentro de esta misma regla general.

**UX (no es regla de backend, es requisito de Fase F):** antes de confirmar la transición `PREPARING → DISPATCHED`,
el frontend muestra diálogo de confirmación ("¿Está seguro de que desea despachar esta carga?"). El endpoint
`POST /shipments/{id}/transitions` no necesita un flag de confirmación adicional en el payload — la confirmación
es responsabilidad de la interfaz, no de la API.

## Pendiente de precisar (asunciones tomadas, confirmar o corregir)

1. **Alcance del retroceso:** se asumió que todo cambio hacia atrás mueve el shipment al estado inmediatamente
   anterior únicamente (nunca salta varios estados de una vez, ej. `DISPATCHED` no puede ir directo a `RECEIVED`
   saltando `PREPARING` y `STORED`). Si se necesita saltar estados, hay que decidir cuáles saltos están permitidos,
   porque `shipment_status_transitions` (Paso 2.1) necesita pares origen→destino explícitos, no una regla genérica.
2. ~~**Alcance de "carga propia" para `CLIENT_ADMIN`**~~ — **Resuelto en ADR-0004.** Es scope `ORGANIZATION`
   (cualquier carga de su empresa), no scope `OWN` (solo lo que ese usuario creó). ADR-0004 define `OWN` como
   scope disponible en el catálogo pero no lo aplica a esta acción.
3. **Estado destino al reabrir `CANCELLED`:** se asumió que vuelve al estado exacto del que fue cancelada, no
   siempre a `PRE_ALERT`. Esto implica que el evento de cancelación debe guardar `previous_status` para poder
   restaurarlo correctamente.

## Alternativas consideradas

- Permitir retrocesos multi-estado sin restricción de "un paso". Descartada por defecto: multiplica los pares
  válidos en `shipment_status_transitions` y dificulta auditar qué retrocesos son razonables en la operación real.
  Se revisa si el punto 1 de arriba se corrige.

## Consecuencias

- `shipment_status_transitions` (Paso 2.1) se siembra con: 7 transiciones forward, 2 transiciones de cancelación
  (`PRE_ALERT→CANCELLED`, `IN_TRANSIT→CANCELLED`), 2 de reapertura (`CANCELLED→PRE_ALERT`, `CANCELLED→IN_TRANSIT`),
  N transiciones backward de un paso (una por cada par de estados forward consecutivos), y una transición especial
  `DELIVERED→DISPATCHED` con permiso exclusivo `SUPER_ADMIN`.
- Cada transición backward y cada cancelación/reapertura exige permiso específico distinto del permiso de
  transición forward normal — el catálogo de permisos de ADR-0004 debe incluir códigos separados
  (ej. `shipments.transition.forward` vs `shipments.transition.backward` vs `shipments.cancel` vs `shipments.reopen`).
- El campo `note`/justificación deja de ser opcional en las transiciones backward, cancelación y reapertura —
  distinto del resto de transiciones donde es opcional (ver ejemplo de payload en Apéndice C del plan).
