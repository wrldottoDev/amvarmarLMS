# ADR-0013: Movimiento de cargas derivado de una acción de despacho

- **Fecha:** 2026-08-25
- **Estado:** Aprobado
- **Aprobado por:** Otoniel González (revisión técnica) + AMVARMAR (decisión de negocio)

## Contexto

Al construir el Paso 3.3 apareció una contradicción entre dos ADR aprobados:

- **ADR-0004** le da `dispatch_requests.reject` a `OPS_AGENT`, pero **no** `shipments.transition.backward`.
- **ADR-0001** clasifica `DISPATCH_REQUESTED → STORED` como retroceso, que exige `OPS_ADMIN`.

Rechazar un despacho tiene que devolver sus cargas a `STORED`, o quedarían atrapadas en
`DISPATCH_REQUESTED` sin ninguna solicitud que las use. Con las reglas tal como estaban, un `OPS_AGENT`
podía rechazar pero no completar el efecto de su propio rechazo.

El caso del cliente lo vuelve inevitable: un cliente puede cancelar su solicitud y **no tiene ningún
permiso de transición de carga**.

## Decisión

### 1. Una acción de despacho autorizada arrastra el movimiento de sus cargas

Quien puede ejecutar una acción sobre una solicitud puede mover las cargas que esa solicitud reclama. Es la
consecuencia de una sola decisión autorizada, no una segunda operación que deba pedir su propio permiso.

Aplica en las dos direcciones, porque el problema es el mismo:

| Acción autorizada | Movimiento derivado | Permiso que se agrega |
|---|---|---|
| `dispatch_requests.create` | `STORED` a `DISPATCH_REQUESTED` | `shipments.transition.forward` |
| `.approve`, avance a `PREPARING` / `COMPLETED` | avance de la carga | `shipments.transition.forward` |
| `.cancel`, `.reject` | vuelta a `STORED` | `shipments.transition.backward` |

El caso de la creación es tan forzoso como el del rechazo: un cliente tiene `dispatch_requests.create` y
ningún permiso de transición, así que sin esto no podría crear ninguna solicitud.

Mover una carga a mano, fuera de un despacho, sigue exigiendo su permiso propio
(`shipments.transition.forward` / `.backward`). Son cosas distintas: una es el efecto de una acción de
despacho que el actor acaba de ejecutar sobre esa misma carga; la otra es corregir a mano el estado de una
carga, que es más riesgoso y menos acotado.

La devolución queda auditada indicando que la causó el rechazo o la cancelación, con el número de la
solicitud. En la línea de tiempo de la carga se ve el motivo, no un retroceso sin explicación.

**Cómo se implementa sin abrir un agujero:** `_permisos_de_la_accion()` construye explícitamente el
conjunto de permisos del paso interno, agregando el de transición que esa acción necesita y **solo** después
de que la acción externa ya fue autorizada, acotado a esa misma empresa. No hay bandera global de "saltear
permisos" ni ruta que evite la verificación.

### 2. El cliente cancela solo antes de la aprobación

| Actor | Puede cancelar desde |
|---|---|
| Cliente (`CLIENT_ADMIN`, `CLIENT_USER`) | `PENDING` únicamente |
| Operaciones (`OPS_AGENT`, `OPS_ADMIN`, `SUPER_ADMIN`) | `PENDING`, `APPROVED` o `PREPARING` |

Una vez que Operaciones aprobó la solicitud, el cliente ya no la cancela: puede haber contenedor
reservado, transporte contratado o carga en movimiento. Si necesita detenerla, lo pide a Operaciones, que
decide.

Permiso nuevo: **`dispatch_requests.cancel`**, otorgado a los cinco roles. La restricción por estado no
vive en el permiso sino en la política de dominio, que compara el estado actual contra el rol — igual que
`shipments.cancel.prealert` y `.in_transit` resuelven el mismo problema para las cargas.

### 3. Doble confirmación antes de que un cliente solicite un despacho

Solicitar un despacho es una acción con consecuencias operativas reales, y a partir de la aprobación el
cliente ya no puede echarse atrás. La interfaz debe pedir una confirmación explícita antes de enviar,
mostrando qué cargas se incluyen.

Es requisito de interfaz (Fase F), no del backend — igual que el diálogo de `PREPARING → DISPATCHED` de
ADR-0001. El backend no lleva una bandera de "confirmado": una confirmación que el cliente puede fabricar
en el payload no confirma nada.

## Alternativas consideradas

- **Quitarle `dispatch_requests.reject` a `OPS_AGENT`.** Descartada: no resuelve el caso del cliente, que
  no tiene permisos de transición y aun así debe poder crear y cancelar.
- **Darle permisos de transición de carga a los roles de cliente.** Descartada: les permitiría mover cargas
  a mano por el endpoint de transiciones, mucho más de lo que necesitan para despachar.
- **Dejar las cargas en `DISPATCH_REQUESTED` tras un rechazo.** Descartada: quedarían sin solicitud que las
  reclame y sin poder entrar en otra, porque crear una solicitud exige que estén `STORED`.
- **Una bandera de "modo sistema" que saltee la verificación de permisos.** Descartada: convierte cualquier
  llamada interna en una vía para eludir el RBAC. El conjunto de permisos aumentado y acotado a la acción
  es explícito y se puede auditar leyendo el código.

## Consecuencias

- Permiso nuevo `dispatch_requests.cancel` en ADR-0004 y en `scripts/seed_rbac.py` — 32 en total.
- La política de cancelación compara estado contra rol, así que el servicio de despachos necesita saber si
  el actor es cliente o interno. Se deriva de si tiene alcance `ORGANIZATION` o `GLOBAL`, no de un campo
  nuevo.
- Fase F suma el diálogo de confirmación al crear una solicitud.
- Los eventos de `dispatch_events` y de `shipment_events` de un movimiento derivado llevan el número de la
  solicitud en el texto, para poder reconstruir qué pasó sin cruzar tablas a mano.
- Toda acción nueva de despacho que mueva cargas debe pasar por `_permisos_de_la_accion()` y declarar qué
  permiso de transición deriva. Agregar la llamada a `_mover_carga()` con los permisos del actor tal cual
  rompería a los clientes, no a los internos, así que no se nota sin una prueba con rol de cliente.
