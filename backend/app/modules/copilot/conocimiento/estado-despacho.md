# Qué significa cada estado de un despacho y cómo lo gestiono

<!-- palabras_clave: estado despacho, aprobar despacho, rechazar despacho, cancelar despacho, completar despacho, registrar salida -->

El ciclo normal de una solicitud de despacho es:

`PENDING` (recibida) → `APPROVED` (Operaciones la aceptó) → `PREPARING` (se están alistando las cargas)
→ `DISPATCHED` (las cargas ya salieron de bodega) → `COMPLETED` (cierre administrativo del despacho).

También puede terminar en `REJECTED` (Operaciones la rechazó, con motivo) o `CANCELLED` (cancelación).

En el detalle del despacho:

- **Operaciones** ve los botones **Aprobar** / **Rechazar** mientras está `PENDING`, **Registrar salida**
  para pasarla a `DISPATCHED`, **Completar despacho** para cerrarla, y **Cancelar** mientras está
  `PENDING`, `APPROVED` o `PREPARING`.
- **El cliente** puede pedir **Cancelar solicitud** únicamente mientras el despacho sigue en `PENDING`.
- Ninguna cancelación es posible después de `DISPATCHED`.
- `COMPLETED` no marca automáticamente las cargas como entregadas: la entrega (`DELIVERED`) de cada
  carga es un paso posterior y separado.
