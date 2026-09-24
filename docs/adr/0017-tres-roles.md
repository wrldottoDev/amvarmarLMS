# ADR-0017: Tres roles, y el cliente deja de registrar cargas

- **Fecha:** 2026-09-07
- **Estado:** Aprobado (2026-09-07)
- **Aprobado por:** Otoniel Gonzalez

## Contexto

El sistema tenía cinco roles: `SUPER_ADMIN`, `OPS_ADMIN`, `OPS_AGENT`,
`CLIENT_ADMIN` y `CLIENT_USER`. Dos observaciones lo volvieron insostenible:

1. **Los dos roles de cliente ya compartían matriz idéntica.** ADR-0016 los
   había unificado en la práctica; quedaban dos códigos para una sola cosa, y
   dar de alta a alguien obligaba a elegir entre dos nombres sin consecuencia.
2. **La operación de AMVARMAR es un solo equipo.** La separación entre agente y
   jefe existía para limitar quién retrocedía un estado o exoneraba un
   requisito, pero en la práctica quien atiende el mostrador es también quien
   corrige el error, y la restricción solo generaba pedidos internos.

Además, el modelo de negocio no era el que el código asumía. El código dejaba
que el cliente creara sus propias prealertas. En la operación real, la carga la
recibe AMVARMAR en Miami, con la factura del proveedor a la vista: el cliente
no la registra, la ve llegar.

## Decisión

### Tres roles

| Rol | Quién es | Alcance |
|---|---|---|
| `SUPER_ADMIN` | Administración técnica y de seguridad | `GLOBAL` |
| `ADMIN` | Personal de AMVARMAR | `GLOBAL` o `ASSIGNED` |
| `CLIENTE` | Empresa cliente y su gente | `ORGANIZATION` |

`OPS_ADMIN` y `OPS_AGENT` se fusionan en `ADMIN`; `CLIENT_ADMIN` y
`CLIENT_USER` en `CLIENTE`. `SUPER_ADMIN` no cambia: sigue siendo el único que
revierte una entrega ya cerrada y el único que gestiona roles y ajustes.

### El cliente no registra cargas

`CLIENTE` pierde cuatro permisos:

- `shipments.create` — las cargas las registra AMVARMAR desde Miami.
- `shipments.update` y `shipments.cancel.prealert` — sin creación no hay
  prealerta propia que editar ni cancelar.
- `users.manage` — a la gente de una empresa la da de alta AMVARMAR.

Le queda lo suyo: ver su inventario (`shipments.read`), pedir despachos
eligiendo la vía (`dispatch_requests.create`, marítimo/aéreo/terrestre),
cancelarlos antes de que los aprueben (`dispatch_requests.cancel`, ADR-0013),
subir los documentos que le exigen (`documents.upload.client`), exportar y
consultar a AMVI.

### AMVI para el cliente es una guía

`CLIENTE` conserva `copilot.use` pero pierde `copilot.tools.draft`: pregunta
dónde está su carga, qué documento le falta o cómo se pide un despacho, y AMVI
lo lleva. No le prepara acciones — quien registra es AMVARMAR.

Como contrapartida, `crear_prealerta_borrador` gana un argumento `empresa`: el
personal de AMVARMAR no tiene empresa propia de la cual deducir el dueño de la
carga, así que lo nombra en la conversación. Si el nombre es ambiguo o no
existe, la herramienta pregunta en vez de elegir — registrar en el expediente
del cliente equivocado es peor que no registrar.

## Consecuencias

- **Migración `a3f1c92e4b70`.** Renombra `OPS_ADMIN`→`ADMIN` y
  `CLIENT_ADMIN`→`CLIENTE` (conservando el `role_id`, para no tocar las
  asignaciones existentes), reapunta las de `OPS_AGENT` y `CLIENT_USER`, y
  recién entonces borra los roles vacíos. Las 28 asignaciones vivas quedaron
  en 8 `ADMIN`, 16 `CLIENTE` y 4 `SUPER_ADMIN`.
- **El downgrade no es fiel.** Quién era agente y quién jefe no queda
  registrado en ningún lado; al bajar, todos quedan en el rol de más capacidad
  de su par. Es aceptable porque la fusión es la decisión, no un accidente.
- **Tests que probaban la diferencia entre pares desaparecieron o cambiaron de
  signo.** `test_un_agente_no_puede_exonerar` se eliminó (la restricción ya no
  existe, y el camino feliz ya estaba cubierto);
  `test_un_agente_no_cancela_en_transito` pasó a comprobar que sí puede. El
  invariante de ADR-0013 —rechazar un despacho devuelve las cargas sin
  apoyarse en `transition.backward`— se conserva armando los permisos a mano,
  porque ya no hay un rol que carezca de ese código.
- **El panel del cliente pierde "Usuarios"** y su listado de cargas pasa a
  llamarse "Inventario", que es lo que es para él: lo que tiene en bodega, no
  cargas que dio de alta.
- **Queda pendiente** revisar las guías de AMVI que describen el flujo viejo
  (`crear-carga.md`, `usuarios-de-mi-empresa.md`): un cliente que pregunte
  "¿cómo creo una carga?" hoy recibiría instrucciones que ya no puede seguir.

## Enmienda — AMVI le prepara la solicitud de despacho al cliente (2026-09-24)

Pedido de Operaciones: que AMVI acompañe al cliente en todo el proceso de despacho, no solo que le
explique cómo hacerlo. Se revierte **en parte** la decisión de arriba: `CLIENTE` vuelve a tener
`copilot.tools.draft`.

**Qué habilita y qué no.** Las herramientas de propuesta exigen `copilot.tools.draft` más un permiso de
dominio. El cliente solo tiene el de `proponer_despacho` (`dispatch_requests.create`, que ya tenía para la
pantalla), así que es la única que alcanza. Crear cargas, avanzar estados y leer documentos internos
siguen siendo de AMVARMAR porque exigen `shipments.create`, `shipments.transition.forward` y
`documents.upload.internal`, que el cliente no tiene.

**Adjuntos del chat.** Hasta ahora se habilitaban solo con `copilot.tools.draft`. Leer un adjunto es una
llamada extra al proveedor y lo leído solo alimenta el alta de una carga. Por eso pasan a exigir también
`shipments.create` (`copilot.router.puede_adjuntar`), y el cliente sigue sin poder adjuntar.

**Cómo funciona.** El cliente nombra las cargas por número, factura o ID. AMVI las resuelve por
coincidencia exacta y única dentro de su empresa, verifica que estén almacenadas y avisa qué requisitos
van a frenar la aprobación. La solicitud se crea recién cuando el cliente pulsa **Confirmar**, con
`dispatches.service.crear`, el mismo camino que `POST /dispatches`: reclamo exclusivo de las cargas, paso
a `DISPATCH_REQUESTED`, línea de tiempo, acuse al cliente y aviso a Operaciones. La empresa y las cargas
salen de la propuesta guardada; al confirmar solo se pueden corregir el método, la dirección, las
instrucciones y la fecha.

El `CHECK` de `copilot_action_proposals.action_code` se amplía en la migración `e7b1f3c9a852`.

