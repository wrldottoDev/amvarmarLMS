# Qué significa cada estado de una carga

<!-- palabras_clave: estado carga, ciclo de una carga, que significa el estado, seguimiento de carga, cambiar estado, pasar a bodega, recibida, almacenada -->

En la pantalla del cliente cada carga dice dónde está: "En camino a Miami", "En Miami", "En Miami ·
preparando despacho", "En camino a Costa Rica" o "En Costa Rica". Los reportes de tránsito dicen "En
origen", "En tránsito a Costa Rica" o "En Costa Rica". Debajo aparece el estado exacto.

El ciclo de una carga de Miami es:

`PRE_ALERT` (registrada, todavía no llegó) → `IN_TRANSIT` (en camino) → `RECEIVED` (recibida en bodega)
→ `STORED` (almacenada, lista para despacharse) → `DISPATCH_REQUESTED` (con un despacho solicitado) →
`PREPARING` → `DISPATCHED` (salió de bodega) → `DELIVERED` (entregada).

El de una carga marítima que va directo a destino (reporte de tránsito) es:

`PRE_ALERT` (prealerta) → `BOOKING_ASSIGNED` (booking asignado con la naviera) → `IN_TRANSIT` (navegando)
→ `TRANSSHIPMENT` (transbordo) → `AT_DESTINATION` (llegó a destino) → `DELIVERED` (entregada). Estas cargas
pueden indicar si van en contenedor completo (FCL) o consolidadas (LCL).

`CANCELLED` es un estado final para cargas que se cancelan antes de recibirse definitivamente.

Los estados los cambia AMVARMAR. El personal de AMVARMAR puede pasar una carga a cualquier estado cuando
hace falta corregirla; cada cambio queda en la línea de tiempo y al cliente le llega el aviso. No existe
un estado `HOLD` separado.

## Avanzar el ingreso a bodega desde AMVI

El personal de AMVARMAR puede pedirle a AMVI que avance una carga dentro del ingreso a bodega:
`PRE_ALERT` → `IN_TRANSIT` → `RECEIVED` → `STORED`. La carga se nombra por su WR o factura y tiene que
coincidir exacto con una sola carga; si la referencia está en varias, AMVI muestra las coincidencias y
pide elegir la correcta.

AMVI solo prepara la propuesta: el cambio se aplica cuando la persona pulsa **Confirmar** en el chat. Si la
carga está varios estados antes, la propuesta incluye todos los pasos y se aplican juntos o ninguno. Si un
paso está bloqueado (por ejemplo, falta el número WR para almacenar en Miami), AMVI
explica qué falta y no propone nada.

Retroceder, cancelar, despachar y entregar no se hacen desde AMVI: se hacen desde la carga.
