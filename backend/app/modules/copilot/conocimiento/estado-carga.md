# Qué significa cada estado de una carga

<!-- palabras_clave: estado carga, ciclo de una carga, que significa el estado, seguimiento de carga, cambiar estado, pasar a bodega, recibida, almacenada -->

El ciclo logístico normal de una carga es:

`PRE_ALERT` (registrada, todavía no llegó) → `IN_TRANSIT` (en camino) → `RECEIVED` (recibida en bodega)
→ `STORED` (almacenada, lista para despacharse) → `DISPATCH_REQUESTED` (con un despacho solicitado) →
`PREPARING` → `DISPATCHED` (salió de bodega) → `DELIVERED` (entregada).

`CANCELLED` es un estado final para cargas que se cancelan antes de recibirse definitivamente.

Los cambios de estado siempre pasan por el motor de transiciones del sistema: un retroceso (por ejemplo,
de `STORED` a `RECEIVED`) requiere permiso de Operaciones y una justificación, y revertir una entrega
(`DELIVERED`) requiere el permiso más alto del sistema. No existe un estado `HOLD` separado.

## Avanzar el ingreso a bodega desde AMVI

El personal de AMVARMAR puede pedirle a AMVI que avance una carga dentro del ingreso a bodega:
`PRE_ALERT` → `IN_TRANSIT` → `RECEIVED` → `STORED`. La carga se nombra por su número (`SHP-…`), su número
de factura o su ID, y tiene que coincidir exacto con una sola carga; si la factura está en varias, AMVI
muestra cuáles y pide elegir por número.

AMVI solo prepara la propuesta: el cambio se aplica cuando la persona pulsa **Confirmar** en el chat. Si la
carga está varios estados antes, la propuesta incluye todos los pasos y se aplican juntos o ninguno. Si un
paso está bloqueado (por ejemplo, falta el WR para almacenar en Miami o un documento obligatorio), AMVI
explica qué falta y no propone nada.

Retroceder, cancelar, despachar y entregar no se hacen desde AMVI: se hacen desde la carga.
