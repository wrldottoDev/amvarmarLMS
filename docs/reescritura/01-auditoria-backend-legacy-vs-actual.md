# Auditoria tecnica del flujo operativo

Fecha de corte: 2026-08-26

## Estado de resolución (actualizado 2026-09-02)

Esta auditoria describe el estado del backend en la fecha de corte. Todos los puntos marcados
"No cumple" o "Bloqueante" en la matriz de abajo (piezas, peso, `provided_by`, verificación
documental, aislamiento de documentos de despacho, hash/ZIP por streams) están resueltos en el
código actual, junto con la unicidad de WR por facility, la búsqueda combinada, el alcance
`DISPATCHED` y el retiro de `PICKUP`. La sección "Evidencia de verificación actual" al final de
este documento queda **obsoleta**: no describe la corrida más reciente. Los números reales de
pruebas, lint, tipos y build están en
[`docs/reescritura/06-implementacion-y-verificacion.md`](./06-implementacion-y-verificacion.md).
Este documento se conserva como registro histórico de lo que motivó cada corrección.

## Alcance

Esta auditoria compara:

- Backend actual FastAPI de `amvarmarLMS`, commit `12ad428`.
- Backend Django del archivo `amvarmarProduccion-main.zip`, commit `31b5c7a`.
- Comportamiento esperado definido por el nuevo alcance del proyecto.

Los documentos de arquitectura y ADR se usaron como referencia para distinguir una
regresion de una decision intencional. La autenticacion actual queda fuera del alcance de
cambio y debe conservarse intacta.

## Resultado ejecutivo

El backend actual ya resuelve correctamente la identidad estable de la carga, el aislamiento
por empresa, RBAC, auditoria, concurrencia, almacenamiento privado y transiciones de estado.
No debe reemplazarse por la logica directa del legacy.

Los ajustes obligatorios se concentran en seis areas:

1. Hacer que una carga siempre tenga al menos una pieza.
2. Sincronizar kilos y libras a partir de una fuente de peso explicita.
3. Ampliar la busqueda a SHP, WR, shipper, carrier y referencias combinables.
4. Hacer cumplir `provided_by` y la relacion documento-requisito.
5. Separar completamente los documentos de carga y de despacho.
6. Mover hashes y ZIP grandes a procesamiento secuencial en workers.

## Matriz de discrepancias

| Requisito | Legacy | Backend actual | Estado | Prioridad |
| --- | --- | --- | --- | --- |
| WR como referencia | El WR es la PK de `Warehouse` | `Shipment` usa UUID y `shipment_number`; WR vive en `shipment_references` | Cumple | Cerrado |
| Una pieza minima | El formset impide guardar sin piezas | `packages` acepta lista vacia | No cumple | Bloqueante |
| Piezas en actualizacion | El formulario permite agregar, editar y quitar piezas | El `PATCH` de carga no acepta piezas ni existe endpoint de reemplazo | No cumple | Bloqueante |
| Archivos por carga | Tras crear, redirige al panel de archivos | Existe expediente en detalle y pagina posterior al alta | Cumple parcialmente | Alta |
| Conversion kg/lb | `Warehouse.save()` completa el peso faltante | Guarda ambos valores sin convertirlos | No cumple | Alta |
| Estado desde vista principal | El listado permite cambios directos sin historial | El motor seguro existe, pero el control solo esta en el detalle | Cumple parcialmente | Alta |
| Busqueda global | Busca WR, shipper y carrier | `q` busca SHP y valores de referencias | Cumple parcialmente | Alta |
| `provided_by` | No tiene tipos ni separacion fina | Solo admite `CLIENT`/`STAFF`, mezcla emisor con uploader y la subida no lo aplica | No cumple | Bloqueante de seguridad |
| Verificacion consistente | No existe el concepto | Se puede marcar `VERIFIED` sin documento `READY` asociado | No cumple | Bloqueante de integridad |
| Documento de despacho aislado | BL e invoices de despacho tienen tablas separadas | El documento se enlaza tambien a la primera carga | No cumple | Bloqueante de privacidad |
| Hash por streams | Archivos locales y operaciones sincronas | `complete` descarga el objeto completo a RAM | No cumple | Alta |
| ZIP por streams | Generacion sincronica | Descarga todos los objetos y arma el ZIP completo en RAM | No cumple | Alta |

## Ubicaciones exactas

### Piezas

- `backend/app/modules/shipments/router.py:127`: `packages` usa `default_factory=list` y
  no tiene `min_length=1`.
- `backend/app/modules/shipments/gestion.py:138`: la transaccion de creacion ya es el lugar
  correcto para validar e insertar la coleccion completa.
- `backend/app/modules/shipments/gestion.py:290`: inserta piezas, pero deja intacto
  `shipments.package_count`.
- `frontend/src/app/(admin)/cargas/nueva/page.tsx:445`: la interfaz permite continuar con
  cero piezas.
- `frontend/src/app/(admin)/cargas/[id]/editar/page.tsx`: no incluye editor de piezas.

Consecuencia actual: una carga puede mostrar `0 bultos`, ser almacenada y entrar en un
despacho aunque no exista desglose fisico.

### Peso

- `backend/app/modules/shipments/gestion.py:170`: solo comprueba que al menos uno de los dos
  campos no sea `None`.
- `backend/app/modules/shipments/models.py:219`: ambos pesos se conservan como valores
  independientes y no existe unidad fuente.
- `frontend/src/app/(admin)/cargas/nueva/page.tsx:117`: envia kg y lb tal como fueron escritos.
- `frontend/src/app/(admin)/cargas/[id]/editar/page.tsx:140`: los inputs tampoco estan
  sincronizados.

Consecuencia actual: se pueden guardar valores inconsistentes, peso cero o solo una unidad.

### Estados

- `backend/app/modules/shipments/router.py:540`: ya existe el endpoint auditable de
  transiciones.
- `backend/app/modules/shipments/service.py:158`: valida permisos, version, requisitos y
  catalogo antes del cambio.
- `frontend/src/app/(client)/shipments/[id]/page.tsx:118`: el control solo aparece en el
  detalle.
- `frontend/src/components/shipments/listado-cargas.tsx`: la tabla no ofrece accion de estado.

El requisito de edicion directa debe resolverse invocando el motor existente desde la tabla.
Agregar `status` al `PATCH /shipments/{id}` reintroduciria la falla del legacy y eliminaria la
garantia de auditoria.

### Busqueda

- `backend/app/modules/shipments/queries.py:183`: `q` combina `shipment_number` y
  `shipment_references.value`.
- `shipper` y `carrier` no forman parte de la condicion.
- No hay parametros independientes para combinar SHP, WR, shipper, carrier y referencia.
- `frontend/src/components/shipments/filtros-cargas.tsx:49`: la interfaz ofrece un unico texto
  generico y filtros de estado/ETA.

### Documentos y requisitos

- `backend/app/modules/documents/catalog.py`: `provided_by` solo admite `CLIENT` o `STAFF`; no
  representa documentos emitidos por proveedor que pueden subir cliente o staff.
- `backend/app/modules/documents/router.py:134`: basta tener cualquiera de los permisos de
  subida; no se contrasta el tipo seleccionado con `provided_by`.
- `backend/app/modules/shipments/service.py:461`: `resolver_requisito()` cambia el estado sin
  exigir un `document_id` ni comprobar `documents.upload_status = 'READY'`.
- `backend/app/modules/documents/service.py:904`: la subida de despacho reutiliza la primera
  carga y crea ambos enlaces.

Consecuencia actual: un cliente puede intentar usar tipos internos, un requisito puede quedar
verificado sin evidencia y un BL global puede aparecer en el expediente de una sola carga.

### Memoria y trabajos grandes

- `backend/app/modules/documents/service.py:272`: SHA-256 usa `leer_completo()`.
- `backend/app/modules/documents/service.py:754`: el ZIP de carga usa `io.BytesIO()`.
- `backend/app/modules/documents/service.py:978`: el ZIP de BL repite el mismo patron.
- `backend/app/infrastructure/storage/s3.py:137`: `leer_completo()` materializa todo el objeto.
- El proyecto ya tiene Celery en `backend/app/workers/`, pero documentos no lo utiliza.

Con el limite actual de 250 MB por PDF y lotes de hasta 1 GB, varios requests concurrentes
pueden agotar la memoria de los workers.

## Hallazgos adicionales

- Crear directamente una carga en `RECEIVED` o `STORED` evita parte de las politicas y de la
  apertura de requisitos que ocurre durante las transiciones.
- No esta implementada la unicidad de WR por facility; hoy dos cargas pueden registrar el mismo
  WR en la misma bodega.
- La respuesta resumida selecciona `company_id`, pero el schema no lo devuelve. La columna de
  empresa de Operaciones queda vacia.
- Cancelar un despacho desde `PREPARING` intenta una transicion de carga no permitida hacia
  `STORED`.
- El estado `DISPATCHED` del despacho esta definido pero no es alcanzable por los endpoints.
- El backend admite `PICKUP`, pero el catalogo aprobado queda limitado a `SEA`, `AIR` y `LAND`.
- El flujo `DELIVERED` aun no captura receptor ni evidencia conforme al ADR-0006.
- El migrador consulta `core_pieceitem`, tabla eliminada en el esquema final del ZIP legacy.
- El metodo legacy `TERRESTRE` cae por defecto en `SEA` durante la migracion.

Estos hallazgos no cambian la autenticacion, pero deben entrar al backlog previo al cutover.

## Criterios de aceptacion de la correccion

- Crear una carga con cero piezas responde `422` y no inserta ninguna fila.
- Reemplazar piezas con una lista vacia responde `422` y conserva la coleccion anterior.
- `package_count` equivale a la suma de `quantity` de las piezas activas.
- Al introducir kg, API y UI calculan lb; al introducir lb, calculan kg con el mismo redondeo.
- Un cambio de estado desde el listado genera `shipment_event`, `audit_log` y aumenta
  `row_version`.
- Los filtros explicitos se combinan con `AND`; `q` busca con `OR` dentro de todos los campos
  soportados y siempre conserva el scope por empresa.
- Un cliente recibe `404` al intentar un tipo `STAFF`; el intento queda auditado sin filtrar el
  recurso.
- `CLIENT_USER` y `CLIENT_ADMIN` producen la misma matriz de permisos dentro de su empresa.
- Solo se aceptan despachos `SEA`, `AIR` y `LAND`.
- `VERIFIED` exige un documento del mismo shipment y tipo, en estado `READY` y no eliminado.
- Un documento de despacho no crea ninguna fila en `shipment_documents`.
- Hash y ZIP procesan archivos mayores al limite de prueba con memoria acotada por chunk.
- Las pruebas de concurrencia demuestran que dos ediciones no pierden piezas ni estados.

## Evidencia de verificacion actual

- Backend FastAPI con `DEBUG=false`: 875 pruebas aprobadas y 1 omitida por falta de
  `DATABASE_URL` externa.
- Ruff sobre `app`, `scripts` y `tests`: sin hallazgos.
- Suite del ZIP legacy: 8 de 9 pruebas aprobadas; falla la subida administrativa de BL.
