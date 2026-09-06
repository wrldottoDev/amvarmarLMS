# Qué significa cada estado de una carga

<!-- palabras_clave: estado carga, ciclo de una carga, que significa el estado, seguimiento de carga -->

El ciclo logístico normal de una carga es:

`PRE_ALERT` (registrada, todavía no llegó) → `IN_TRANSIT` (en camino) → `RECEIVED` (recibida en bodega)
→ `STORED` (almacenada, lista para despacharse) → `DISPATCH_REQUESTED` (con un despacho solicitado) →
`PREPARING` → `DISPATCHED` (salió de bodega) → `DELIVERED` (entregada).

`CANCELLED` es un estado final para cargas que se cancelan antes de recibirse definitivamente.

Los cambios de estado siempre pasan por el motor de transiciones del sistema: un retroceso (por ejemplo,
de `STORED` a `RECEIVED`) requiere permiso de Operaciones y una justificación, y revertir una entrega
(`DELIVERED`) requiere el permiso más alto del sistema. No existe un estado `HOLD` separado.
