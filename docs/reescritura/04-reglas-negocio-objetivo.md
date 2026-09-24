# Reglas de negocio objetivo

- Estado: Aprobado para implementacion
- Fecha: 2026-08-26
- Alcance: Reescritura FastAPI + Next.js

## Proposito

Estas reglas conservan el flujo operativo util del sistema Django, pero usan el modelo de dominio,
seguridad y trazabilidad del sistema nuevo. Los nombres `Warehouse`, `AdminProfile`, `ClientProfile`
y otros modelos legacy describen el origen historico, no el contrato de la nueva aplicacion.

La autenticacion actual del backend FastAPI se mantiene intacta. Los cambios de este documento
afectan autorizacion de dominio, cargas, documentos, despachos, notificaciones y busqueda.

## 1. Autenticacion, roles y acceso

### 1.1 Roles

El sistema usa los cinco roles RBAC actuales:

| Rol | Alcance | Responsabilidad |
| --- | --- | --- |
| `SUPER_ADMIN` | Global | Seguridad, RBAC, configuracion y operaciones excepcionales |
| `OPS_ADMIN` | Global | Administracion de operaciones, empresas, usuarios, correcciones y auditoria |
| `OPS_AGENT` | Global o asignado | Recepcion, almacenamiento, documentos y despachos cotidianos |
| `CLIENT_ADMIN` | Su empresa | Usuarios, prealertas, cargas, documentos y despachos de su empresa |
| `CLIENT_USER` | Su empresa | Las mismas capacidades de `CLIENT_ADMIN` dentro de su empresa |

No existen `is_staff`, `AdminProfile` ni `ClientProfile` como fuentes de autorizacion. La identidad
sale de `users`; la pertenencia empresarial sale de `company_memberships`; las capacidades salen de
roles, permisos y scope.

`CLIENT_USER` y `CLIENT_ADMIN` conservan codigos separados por compatibilidad y futura evolucion,
pero en este alcance tienen la misma matriz de permisos. Ambos pueden crear y corregir prealertas,
gestionar usuarios de su empresa, subir documentos permitidos y crear o cancelar solicitudes de
despacho. Ninguno puede ejecutar transiciones logisticas manuales reservadas a Operaciones.

### 1.2 Aislamiento por empresa

- Todo recurso de negocio pertenece a una `Company` directa o indirectamente.
- Los clientes solo acceden a recursos de su empresa.
- Un `OPS_AGENT` con scope `ASSIGNED` solo accede a recursos que tenga asignados.
- Una consulta por UUID fuera del scope responde `404`, igual que un recurso inexistente.
- Un `403` se reserva para acciones generales conocidas por el actor, por ejemplo intentar crear una
  carga en una empresa sobre la que no tiene permiso.
- El filtro de empresa se aplica en SQL antes de leer la fila, nunca despues en Python.
- Ocultar botones en Next.js mejora la interfaz, pero no constituye control de acceso.

### 1.3 Reglas de seguridad transversales

- Toda mutacion sensible deja `audit_log` con actor, recurso, resultado, fecha y `request_id`.
- Los intentos denegados relevantes tambien se auditan sin revelar datos de otra empresa.
- Las actualizaciones usan `row_version`; una version obsoleta responde `409`.
- Las creaciones con efectos repetibles usan `Idempotency-Key`.
- Los registros operativos y documentos se invalidan u ocultan; no se borran fisicamente.

## 2. Recepcion y gestion de cargas

### 2.1 Identidad e identificadores

- `Shipment.id` es un UUID interno, estable y no visible como referencia comercial principal.
- `shipment_number` es el numero legible `SHP-YYYY-NNNNNN`, unico y no editable.
- El Warehouse Receipt es una referencia `WR`; nunca es PK ni identidad de la carga.
- Una carga puede tener factura, WR, PO, tracking, contenedor, BL y referencias adicionales.
- WR solo aplica cuando la facility de origen tiene `uses_warehouse_receipt = true`.
- Un WR es unico dentro de la facility que lo emitio, comparado de forma normalizada.
- No se infiere Miami por shipper, carrier ni texto libre.

### 2.2 Piezas

- Toda carga debe tener al menos una fila de pieza desde su creacion.
- Cada pieza exige tipo y `quantity >= 1`.
- Peso y dimensiones por pieza son opcionales, pero si se informan deben ser mayores que cero.
- `package_count` es la suma de `quantity`, no la cantidad de filas.
- Crear, reemplazar o eliminar piezas es una operacion atomica.
- Ninguna actualizacion puede dejar la carga con cero piezas.
- La validacion existe en Pydantic, servicio de dominio y base de datos.
- Una carga legacy sin piezas se marca para correccion y no se completa inventando datos.

### 2.3 Peso

- Toda carga nueva exige un peso fisico mayor que cero.
- El usuario puede escribir kg o lb; el ultimo campo editado es la fuente.
- Frontend y backend convierten usando `1 kg = 2.2046226218487757 lb`.
- El backend recalcula ambas unidades con `Decimal` y redondeo uniforme a tres decimales.
- No se confia en la conversion enviada por el navegador.
- El peso volumetrico es un dato distinto del peso fisico. Es opcional mientras no exista una regla
  aprobada por modo de transporte que lo vuelva obligatorio.
- Los valores legacy no se recalculan durante la migracion. Se normalizan cuando un usuario los edita
  y la correccion queda auditada.

### 2.4 Estados de carga

El ciclo logistico es:

```text
PRE_ALERT -> IN_TRANSIT -> RECEIVED -> STORED
          -> DISPATCH_REQUESTED -> PREPARING -> DISPATCHED -> DELIVERED
```

`CANCELLED` es terminal para cargas canceladas antes de su recepcion definitiva.

- Una carga nueva nace en `PRE_ALERT` por defecto.
- Operaciones puede registrar una carga en un estado inicial posterior, pero el backend recorre las
  transiciones intermedias dentro de la misma transaccion y aplica todas sus politicas.
- Un cambio desde el listado o detalle siempre llama al motor de transiciones.
- No se permite escribir `current_status_code` mediante el `PATCH` general de carga.
- Las transiciones hacia adelante requieren el permiso correspondiente.
- Los retrocesos son de un paso, requieren `OPS_ADMIN` o superior y una justificacion.
- Revertir `DELIVERED` requiere `SUPER_ADMIN` y justificacion.
- Una accion masiva evalua permiso, version y requisitos para cada carga; nunca ejecuta un `UPDATE`
  directo por estado.
- Cada cambio crea evento de carga, auditoria, hitos y outbox en la misma transaccion.
- No se agrega `HOLD` ni otra condicion operativa nueva. Se conserva exclusivamente el catalogo de
  estados ya aprobado. El campo tecnico `legal_hold` existente para retencion legal no aparece como
  estado ni altera este flujo operativo.

## 3. Documentos y archivos

### 3.1 Pertenencia

- Un documento de carga se asocia explicitamente mediante `shipment_documents`.
- Un documento de despacho se asocia exclusivamente mediante `dispatch_documents`.
- Subir un documento al despacho nunca lo liga implicitamente a la primera carga.
- Un documento puede cubrir varias cargas solo mediante enlaces explicitos y autorizados.
- La pantalla de alta y el detalle de la carga conservan un panel de archivos directo.

### 3.2 Emisor y permisos de subida

- Se distinguen dos conceptos: quien emitio el documento y quien puede subirlo a la plataforma.
- `issued_by` registra el origen comercial: `PROVIDER`, `CLIENT`, `AMVARMAR`, `CARRIER`,
  `AUTHORITY` u `OTHER`.
- `provided_by` controla la subida: `CLIENT`, `STAFF` o `CLIENT_OR_STAFF`.
- Un usuario con `documents.upload.client` solo puede usar tipos `CLIENT` o `CLIENT_OR_STAFF` dentro
  de su empresa.
- Un usuario con `documents.upload.internal` puede usar tipos `STAFF` o `CLIENT_OR_STAFF`.
- Los documentos de despacho requieren permiso interno y un tipo habilitado para `DISPATCH`.
- El catalogo que recibe Next.js se filtra por actor y contexto.
- El backend repite la validacion aunque el tipo no aparezca en la interfaz.

### 3.3 Catalogo documental obligatorio

| Documento | Contexto | Emisor | Quien lo sube | Aplicabilidad | Gate |
| --- | --- | --- | --- | --- | --- |
| Factura comercial | Carga | Proveedor o cliente | Cliente o staff | Todas las cargas | Antes de `DISPATCHED` |
| SLI | Carga | Proveedor o cliente | Cliente o staff | Solo facility MIA | Antes de `DISPATCHED` |
| Packing list | Carga | Proveedor | Staff | Todas las cargas | Antes de `DISPATCHED` |
| BL | Despacho | Carrier o AMVARMAR | Staff | SEA, AIR y LAND | Antes de `COMPLETED` |
| Permiso especial | Carga | Cliente o autoridad | Cliente | Solo cuando la carga lo requiere | Antes de `DISPATCHED` |

Un documento emitido por un proveedor no implica que exista un rol `PROVIDER`. El emisor es metadata;
la autorizacion depende de `provided_by` y de los permisos del usuario que efectivamente lo sube.

### 3.4 Seguridad de archivos

- La subida ocurre directamente a storage privado mediante URL firmada de corta duracion.
- Se valida extension temprana, MIME real, firma magica, tamano y formato permitido por tipo.
- SHA-256 se calcula por chunks; el archivo completo nunca se materializa en RAM.
- Los archivos grandes pasan por Celery y usan estados tecnicos:

```text
UPLOADING -> PROCESSING -> READY
                      \-> FAILED
```

- `READY` significa que los bytes son validos y descargables, no que el documento fue aprobado.
- Invalidar un documento conserva bytes, hash, actor, fecha y motivo.
- Las descargas usan URLs firmadas y vuelven a comprobar el scope en cada solicitud.

### 3.5 Requisitos documentales

El requisito tiene un ciclo independiente:

```text
PENDING -> UPLOADED -> VERIFIED
                    \-> REJECTED -> UPLOADED
```

Tambien puede terminar en `NOT_APPLICABLE`, `WAIVED` o `CANCELLED` cuando la politica lo permita.

- Subir un archivo `READY` mueve el requisito a `UPLOADED`, no a `VERIFIED`.
- `VERIFIED` exige seleccionar un documento `READY`, no invalidado, de la misma carga y del mismo
  tipo documental.
- La verificacion requiere `documents.verify` con scope sobre la empresa.
- Rechazar o exonerar exige motivo y conserva toda la evidencia.
- Los requisitos bloqueantes deben estar resueltos antes de la transicion que protegen.

### 3.6 Exportaciones

- Los ZIP de carga y despacho se generan como trabajos asincronos.
- El worker lee y escribe por chunks, usa ZIP64 y almacenamiento temporal privado.
- El API responde con un job consultable, no con una conexion abierta durante toda la generacion.
- El resultado expira y solo puede descargarlo quien conserve acceso al recurso.
- Reintentos no duplican trabajos ni objetos finales.

## 4. Solicitudes de despacho

### 4.1 Elegibilidad y creacion

- Cliente u Operaciones selecciona una o mas cargas de una misma empresa.
- Toda carga debe estar en `STORED`, no oculta, no archivada y sin otro despacho activo.
- La base de datos impide que dos solicitudes activas reclamen la misma carga.
- Los requisitos que bloquean despacho deben resolverse antes de aprobar o preparar, segun el punto
  definido por el catalogo documental.
- Crear la solicitud mueve atomicamente las cargas a `DISPATCH_REQUESTED`.
- La interfaz del cliente exige una confirmacion mostrando las cargas incluidas.

### 4.2 Estados del despacho

El ciclo normal es:

```text
PENDING -> APPROVED -> PREPARING -> DISPATCHED -> COMPLETED
    \-> REJECTED
    \-> CANCELLED
```

- `PENDING`: solicitud recibida y aun no aprobada.
- `APPROVED`: Operaciones acepto la solicitud y sus condiciones.
- `PREPARING`: las cargas se estan alistando fisicamente.
- `DISPATCHED`: las cargas salieron de bodega; cada Shipment pasa a `DISPATCHED`.
- `COMPLETED`: cierre administrativo del despacho; no cambia las cargas a `DELIVERED`.
- `DELIVERED` pertenece a cada carga y exige evidencia de entrega.
- `REJECTED`: rechazo por Operaciones con motivo obligatorio.
- `CANCELLED`: cancelacion autorizada, distinta de un rechazo.

Solo existen los metodos `SEA`, `AIR` y `LAND`. Se elimina `PICKUP` del catalogo, schemas, filtros y
base de datos.

`COMPLETED` representa el final completo de la solicitud: todas sus cargas ya estan `DISPATCHED`,
todos los requisitos documentales aplicables estan `VERIFIED` y existe al menos un BL del despacho
en estado `READY`. Completar no marca automaticamente las cargas como `DELIVERED`; la entrega sigue
siendo un evento posterior de cada carga.

### 4.3 Rechazo y cancelacion

- El cliente puede cancelar solamente desde `PENDING`.
- Operaciones puede cancelar desde `PENDING`, `APPROVED` o `PREPARING`.
- No se cancela despues de `DISPATCHED`; cualquier correccion posterior requiere un flujo auditable
  excepcional.
- Rechazar o cancelar libera el reclamo y devuelve las cargas a `STORED` como una unica operacion de
  dominio.
- El retorno puede cruzar pasos internos, pero deja todos los eventos necesarios y no depende de una
  transicion manual invalida `PREPARING -> STORED`.
- Fallar una carga revierte toda la accion de despacho.

### 4.4 Documentos de despacho

- Un despacho puede tener uno o varios BL y otros documentos master aplicables.
- Los documentos pertenecen al despacho global, no a una carga elegida por conveniencia tecnica.
- Solo personal con permiso interno puede cargarlos, invalidarlos o reemplazarlos.
- El cliente puede consultarlos y descargarlos dentro del scope de su empresa.

## 5. Notificaciones y comunicaciones

### 5.1 Entrega

- Los eventos de negocio se publican mediante outbox dentro de la transaccion que los origina.
- Celery entrega notificaciones `IN_APP` y `EMAIL` con deduplicacion, reintentos y registro de cada
  intento.
- Un fallo de correo no revierte la operacion de negocio ni pierde el evento.
- `IN_APP` y `EMAIL` permanecen activos para los eventos definidos por negocio.

### 5.2 Alta y seguridad de cuenta

- Nunca se envia una contrasena por correo.
- El alta envia un enlace de invitacion de un solo uso y vencimiento limitado.
- Restablecer acceso usa un token de un solo uso; la respuesta no revela si el correo existe.
- Alertas de seguridad se generan por nueva sesion, reutilizacion de refresh y cambio de contrasena.

### 5.3 Eventos operativos

Se notifica, como minimo:

- Carga almacenada o cambio relevante de estado.
- Requisito bloqueante abierto o documento rechazado.
- Solicitud de despacho creada.
- Despacho aprobado, rechazado, cancelado, despachado o completado.
- BL disponible, solo despues de confirmar un documento BL `READY`.
- Carga entregada o estado corregido hacia atras.

El correo puede incluir identificador comercial, nuevo estado y enlace. No incluye shipper, carrier,
pesos, montos, lista completa de cargas, credenciales ni documentos adjuntos.

## 6. Busqueda y filtrado

### 6.1 Busqueda global

Un solo campo `q` busca con OR sobre:

- Numero SHP.
- Numero WR.
- Shipper.
- Carrier.
- Factura, PO, tracking, contenedor, BL y referencias adicionales.

### 6.2 Filtros combinados

Se ofrecen parametros independientes para SHP, WR, shipper, carrier, referencia, tipo de referencia,
empresa, estado y rango ETA.

- Los filtros explicitos se combinan con AND.
- El filtro de scope por empresa siempre se aplica adicionalmente.
- Operaciones puede filtrar por empresa; un cliente no puede ampliar su scope con ese parametro.
- La busqueda ignora mayusculas/minusculas y normaliza espacios de identificadores.
- Los resultados usan paginacion por cursor estable.
- PostgreSQL usa indices adecuados para coincidencias parciales; no se cargan todas las filas en
  memoria para filtrar.
- El frontend conserva filtros en la URL y permite retirarlos individualmente.

## 7. Reglas de interfaz

- Next.js se redisenia como herramienta operativa responsive y accesible.
- Operaciones puede cambiar estados desde la tabla sin abrir el detalle.
- El selector solo muestra transiciones validas, pero FastAPI vuelve a autorizarlas.
- Alta y edicion comparten componentes de peso y piezas para evitar reglas divergentes.
- No se puede quitar la ultima pieza en la interfaz ni mediante API.
- El panel de archivos esta disponible inmediatamente despues de crear y desde el detalle.
- Los clientes nunca ven acciones ni tipos documentales internos.
- Carga, procesamiento, error, vacio y conflicto de version tienen estados visuales definidos.
- La interfaz reduce pasos, pero no oculta confirmaciones para despachos, cancelaciones o retrocesos.

## 8. Reglas de migracion

- La migracion es idempotente y conserva el identificador legacy en un mapa auditable.
- No se inventan estados, piezas, tipos documentales ni metodos de transporte.
- Un valor ambiguo queda marcado para revision manual.
- `TERRESTRE` se traduce explicitamente a `LAND`.
- La migracion usa las tablas realmente presentes en la ultima migracion Django.
- Los documentos legacy sin tipo comprobable se migran como tipo legacy pendiente de clasificacion,
  no como factura comercial.
- El cutover se bloquea si faltan cargas, piezas, documentos o relaciones respecto del inventario
  aprobado.

## Decisiones aprobadas

1. `CLIENT_USER` tiene las mismas capacidades que `CLIENT_ADMIN`, incluido crear prealertas y
   solicitudes de despacho dentro de su empresa.
2. No se agrega `HOLD`; se mantienen los estados originales del backend nuevo.
3. `DISPATCHED` representa la salida fisica y `COMPLETED` el final completo del despacho.
4. Los unicos metodos son `SEA`, `AIR` y `LAND`.
5. Factura comercial, packing list y BL son obligatorios; SLI aplica solo a MIA y permiso especial
   solo cuando corresponda.
6. El peso volumetrico permanece opcional.
