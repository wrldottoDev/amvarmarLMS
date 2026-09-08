# Arquitectura backend para los flujos operativos

## Principios

- La autenticacion, sesiones, JWT, Argon2id y refresh rotation no se modifican.
- `Shipment.id` sigue siendo la identidad estable. SHP y WR son referencias de negocio.
- Un cambio visible de estado siempre pasa por el motor de transiciones.
- Toda mutacion conserva RBAC, scope por empresa, auditoria, idempotencia cuando aplique y
  control optimista con `row_version`.
- El frontend ayuda a prevenir errores, pero el backend vuelve a validar todas las reglas.
- Las operaciones con archivos grandes no mantienen el objeto completo en la RAM de FastAPI.

## Modelo de datos propuesto

### Cargas y piezas

Se conserva `shipments` como raiz del agregado y `shipment_packages` como su coleccion de piezas.

Cambios:

- `shipments.package_count` representa la suma de `shipment_packages.quantity`, no la cantidad de
  filas de tipos de pieza.
- Un trigger mantiene `package_count` despues de `INSERT`, `UPDATE` o `DELETE` de piezas.
- Un constraint trigger diferible valida al `COMMIT` que toda carga activa tenga al menos una
  pieza y `package_count > 0`.
- Creacion y reemplazo de piezas ocurren en una sola transaccion.
- Las cargas legacy sin piezas se detectan antes de habilitar el constraint. No se inventan
  piezas silenciosamente; bloquean el gate de migracion hasta ser corregidas.

### Pesos

Se mantienen `weight_kg` y `weight_lb` para lectura, reportes y compatibilidad con datos legacy.
Para registros nuevos se agrega `weight_source_unit` (`KG` o `LB`) y se recibe un unico valor
fuente.

Regla de conversion:

```text
1 kg = 2.2046226218487757 lb
kg = lb / 2.2046226218487757
```

- Se usa `Decimal`, nunca `float`.
- El valor recibido debe ser mayor que cero.
- Ambos resultados se guardan con tres decimales usando `ROUND_HALF_UP`.
- Si cambia el peso, se reemplazan las dos representaciones en la misma sentencia.
- Los datos legacy conservan sus valores originales. La conversion automatica comienza cuando
  el usuario edita el peso y queda registrada como correccion.

### WR y referencias

- WR permanece en `shipment_references` con `reference_type = 'WR'`.
- La referencia WR solo se permite si `origin_facility.uses_warehouse_receipt = true`.
- Para implementar unicidad por bodega, la referencia WR conserva `facility_id` y un valor
  normalizado. Un indice unico parcial cubre `(facility_id, normalized_value)` cuando el tipo es
  `WR`.
- SHP permanece globalmente unico y no editable.
- Las referencias adicionales siguen admitiendo multiples valores por carga.

### Documentos

`documents` conserva la metadata comun. La pertenencia se expresa exclusivamente mediante una
de estas tablas:

- `shipment_documents`: expediente de una carga.
- `dispatch_documents`: documentos globales de una solicitud de despacho.

Un documento de despacho no crea una fila en `shipment_documents`. La funcion comun de reserva
solo crea `documents`; cada servicio de dominio inserta despues exactamente un enlace padre.

`document_types` separa el emisor comercial de la autorizacion de subida:

- `issued_by_options`: `PROVIDER`, `CLIENT`, `AMVARMAR`, `CARRIER`, `AUTHORITY`, `OTHER`.
- `provided_by`: `CLIENT`, `STAFF` o `CLIENT_OR_STAFF`.
- `context`: `SHIPMENT` o `DISPATCH`.

Cada documento guarda el `issued_by` real seleccionado entre las opciones permitidas por su tipo.

Para exportaciones se agrega `document_export_jobs`:

| Campo | Uso |
| --- | --- |
| `id` | UUID del trabajo |
| `requested_by` | Usuario que solicito la exportacion |
| `company_id` | Scope de seguridad |
| `resource_type` | `SHIPMENT` o `DISPATCH` |
| `resource_id` | Recurso exportado |
| `kind` | `ALL_DOCUMENTS` o `BLS` |
| `status` | `PENDING`, `PROCESSING`, `READY`, `FAILED`, `EXPIRED` |
| `storage_key` | Solo interno, nunca se devuelve por API |
| `size_bytes`, `sha256` | Integridad del resultado |
| `error_code` | Codigo seguro, sin traza ni credenciales |
| `expires_at` | Vencimiento del archivo temporal |

## Schemas Pydantic

Los nombres siguientes siguen las convenciones actuales del backend. Son el contrato objetivo,
no una segunda implementacion paralela.

```python
class UnidadPeso(StrEnum):
    KG = "KG"
    LB = "LB"


class PesoInput(BaseModel):
    value: Decimal = Field(gt=0, max_digits=14, decimal_places=3)
    unit: UnidadPeso


class BultoInput(BaseModel):
    id: UUID | None = None
    package_type: PackageType
    quantity: int = Field(ge=1, le=100_000)
    description: str | None = Field(default=None, max_length=300)
    weight_kg: Decimal | None = Field(default=None, gt=0)
    length_cm: Decimal | None = Field(default=None, gt=0)
    width_cm: Decimal | None = Field(default=None, gt=0)
    height_cm: Decimal | None = Field(default=None, gt=0)


class CrearCargaRequest(BaseModel):
    company_id: UUID
    origin_location_id: UUID
    destination_location_id: UUID
    origin_facility_id: UUID | None = None
    weight: PesoInput
    packages: Annotated[list[BultoInput], Field(min_length=1, max_length=50)]
    initial_status: Literal["PRE_ALERT", "IN_TRANSIT", "RECEIVED", "STORED"] = "PRE_ALERT"
    # Los demas campos comerciales actuales permanecen.


class ActualizarCargaRequest(BaseModel):
    row_version: int = Field(ge=1)
    weight: PesoInput | None = None
    # Solo datos descriptivos, ruta y referencias. El estado no entra aqui.


class ReemplazarBultosRequest(BaseModel):
    row_version: int = Field(ge=1)
    packages: Annotated[list[BultoInput], Field(min_length=1, max_length=50)]


class VerificarRequisitoRequest(BaseModel):
    document_id: UUID
    note: str | None = Field(default=None, max_length=2000)


class PrepararDocumentoRequest(BaseModel):
    document_type_id: UUID
    issued_by: Literal["PROVIDER", "CLIENT", "AMVARMAR", "CARRIER", "AUTHORITY", "OTHER"]
    original_name: str = Field(min_length=1, max_length=255)


class TrabajoExportacionResponse(BaseModel):
    id: UUID
    status: Literal["PENDING", "PROCESSING", "READY", "FAILED", "EXPIRED"]
    progress_percent: int = Field(ge=0, le=100)
    filename: str | None = None
    download_url: str | None = None
    expires_at: datetime | None = None
```

Las respuestas de carga incluyen `company_id`, `company_name`, `row_version`, ambos pesos,
`weight_source_unit`, piezas y `package_count` consistente.

## Endpoints

| Metodo y ruta | Cambio |
| --- | --- |
| `POST /api/v1/shipments` | Exige `weight` y al menos una pieza; crea todo atomicamente |
| `PATCH /api/v1/shipments/{id}` | Corrige datos y peso; no acepta estado ni piezas |
| `PUT /api/v1/shipments/{id}/packages` | Reemplazo atomico de piezas, nunca permite lista vacia |
| `GET /api/v1/shipments` | Amplia filtros combinables y devuelve empresa/version |
| `GET /api/v1/shipments/{id}/transitions/available` | Opciones permitidas para el actor y bloqueos actuales |
| `POST /api/v1/shipments/{id}/transitions` | Se conserva como unica escritura de estado |
| `GET /api/v1/document-types?context=SHIPMENT` | Solo tipos y emisores que el actor puede usar en cargas |
| `POST /api/v1/shipments/{id}/documents/presign` | Aplica `provided_by` antes de reservar |
| `POST /api/v1/shipments/{id}/documents/complete` | Valida metadata y encola procesamiento; responde `202` |
| `POST /api/v1/shipments/{id}/requirements/{rid}/verify` | Exige `document_id` compatible y `READY` |
| `POST /api/v1/shipments/{id}/requirements/{rid}/reject` | Rechaza con motivo y conserva evidencia |
| `POST /api/v1/dispatches/{id}/documents/presign` | Reserva documento ligado solo al despacho |
| `POST /api/v1/dispatches/{id}/documents/complete` | Completa usando el enlace de despacho, sin shipment auxiliar |
| `POST /api/v1/shipments/{id}/document-exports` | Crea ZIP asincrono de expediente |
| `POST /api/v1/dispatches/{id}/document-exports` | Crea ZIP asincrono de documentos o BL |
| `GET /api/v1/document-exports/{job_id}` | Consulta progreso y entrega URL firmada al finalizar |

## Busqueda global y combinada

`GET /api/v1/shipments` acepta:

| Parametro | Semantica |
| --- | --- |
| `q` | OR entre SHP, WR, shipper, carrier y cualquier referencia |
| `shipment_number` | Coincidencia parcial de SHP |
| `wr` | Coincidencia parcial de WR |
| `shipper` | Coincidencia parcial normalizada |
| `carrier` | Coincidencia parcial normalizada |
| `reference` | Valor de cualquier referencia |
| `reference_type` | Limita `reference` a WR, INVOICE, PO, TRACKING, CONTAINER, BL u OTHER |
| `status`, `company_id`, `eta_from`, `eta_to` | Filtros existentes |

`q` usa OR internamente. Todos los filtros explicitos se combinan con AND y siempre se agregan a
las condiciones de scope, nunca las reemplazan.

Para sostener busquedas parciales se habilita `pg_trgm` y se agregan indices GIN trigram sobre:

- `shipments.shipment_number`
- `shipments.shipper`
- `shipments.carrier`
- `shipment_references.value`

La paginacion por cursor actual se conserva.

## Cambio directo de estado sin perder seguridad

La vista principal puede editar el estado, pero no mediante un `PATCH` irrestricto:

1. Al abrir el selector, el frontend consulta las transiciones disponibles.
2. Solo muestra destinos permitidos por estado y rol.
3. Solicita motivo cuando el catalogo lo exige.
4. Envia `to_status`, `row_version`, fecha, ubicacion y nota al endpoint existente.
5. El backend vuelve a validar permiso, scope, requisitos y version.
6. La misma transaccion actualiza carga, hitos, evento, auditoria y outbox.

Para un `initial_status` distinto de `PRE_ALERT`, la creacion recorre internamente cada paso del
catalogo dentro de la misma transaccion. Asi `STORED` no evita WR, requisitos, hitos ni eventos.

## Roles de cliente

`CLIENT_USER` y `CLIENT_ADMIN` mantienen codigos separados, pero reciben la misma matriz de permisos
en este alcance. Ambos gestionan usuarios, prealertas, documentos permitidos y despachos dentro de
su empresa. Ninguno obtiene permisos manuales de transicion logistica.

## Reglas de `provided_by`

La autorizacion se calcula con permisos efectivos, no con botones ocultos:

| Tipo | `documents.upload_client` | `documents.upload_internal` |
| --- | --- | --- |
| `provided_by=CLIENT` | Permitido dentro de su empresa | Denegado |
| `provided_by=STAFF` | Denegado como `404` | Permitido |
| `provided_by=CLIENT_OR_STAFF` | Permitido dentro de su empresa | Permitido |

Los documentos de despacho requieren siempre `documents.upload_internal` y un tipo habilitado
para contexto `DISPATCH`. El endpoint de catalogo aplica la misma matriz para que la UI no ofrezca
opciones imposibles, pero la seguridad vive nuevamente en el servicio.

Catalogo inicial:

| Codigo | Contexto | `provided_by` | Emisores | Aplicacion |
| --- | --- | --- | --- | --- |
| `COMMERCIAL_INVOICE` | `SHIPMENT` | `CLIENT_OR_STAFF` | Proveedor, cliente | Siempre, antes de despachar |
| `SLI` | `SHIPMENT` | `CLIENT_OR_STAFF` | Proveedor, cliente | Solo facility MIA, antes de despachar |
| `PACKING_LIST` | `SHIPMENT` | `STAFF` | Proveedor | Siempre, antes de despachar |
| `BL` | `DISPATCH` | `STAFF` | Carrier, AMVARMAR | Siempre, antes de completar |
| `SPECIAL_PERMIT` | `SHIPMENT` | `CLIENT` | Cliente, autoridad | Solo cuando corresponda |

## Flujo definitivo de despacho

Solo se aceptan `SEA`, `AIR` y `LAND`. La migracion elimina `PICKUP` del CHECK de base, enums,
schemas y filtros despues de comprobar que no existan filas con ese valor.

```text
PENDING -> APPROVED -> PREPARING -> DISPATCHED -> COMPLETED
    \-> REJECTED
    \-> CANCELLED
```

- Crear mueve las cargas `STORED -> DISPATCH_REQUESTED`.
- Preparar mueve `DISPATCH_REQUESTED -> PREPARING`.
- Despachar mueve `PREPARING -> DISPATCHED` y representa la salida fisica.
- Completar no mueve las cargas. Representa que todo el despacho ya quedo cerrado.
- `COMPLETED` exige todas las cargas `DISPATCHED`, requisitos aplicables `VERIFIED` y al menos un
  BL global del despacho en `READY`.
- `DELIVERED` sigue siendo una transicion individual posterior de cada carga.
- Rechazar o cancelar antes de la salida devuelve atomicamente todas las cargas a `STORED`.

## Verificacion de requisitos

Para pasar a `VERIFIED`, el servicio bloquea requisito y documento con `FOR UPDATE` y comprueba:

- El requisito pertenece a la carga indicada.
- Es de tipo `DOCUMENT` y tiene `document_type_id`.
- El documento pertenece a la misma carga.
- El enlace tiene el mismo `document_type_id`.
- `documents.upload_status = 'READY'`.
- El documento no esta invalidado ni eliminado.
- El actor tiene `documents.verify` con scope sobre la empresa.

El `document_id` verificado queda registrado en el requisito o en una tabla de evidencia. Rechazar
no borra el archivo; conserva la version revisada y permite una nueva subida.

## Procesamiento secuencial

### SHA-256

`complete` realiza `HEAD` y lectura de cabecera, cambia `UPLOADING -> PROCESSING`, publica un
trabajo Celery y responde `202`. El worker:

1. Abre el `StreamingBody` de S3.
2. Lee chunks acotados, por ejemplo 8 MiB.
3. Actualiza `hashlib.sha256` sin concatenar bytes.
4. Confirma que el total coincide con `Content-Length`.
5. Marca `READY` o `FAILED` y publica el evento de outbox.

El ETag no reemplaza SHA-256 porque en uploads multipart no representa necesariamente el hash del
contenido.

### ZIP

La API crea un `document_export_job` idempotente y responde `202`. Un worker descarga cada origen
por chunks, genera ZIP64 secuencialmente y sube el resultado mediante multipart a una clave
temporal privada. La memoria maxima depende del chunk, no del tamano total.

El resultado solo puede descargarse por el solicitante o por un actor que todavia tenga scope
sobre la empresa. Una tarea periodica expira el objeto y marca el job `EXPIRED`.

## Pruebas obligatorias

- Creacion y reemplazo de piezas con minimo uno.
- Rollback completo si falla una pieza o el trigger diferible.
- Conversion kg/lb en ambos sentidos, redondeo y rechazo de cero.
- Transicion desde listado con version obsoleta devuelve `409`.
- Busqueda parametrizada por cada campo, combinaciones y aislamiento entre empresas.
- Matriz `provided_by` parametrizada por rol, scope, contexto y tipo.
- Paridad completa de permisos entre `CLIENT_USER` y `CLIENT_ADMIN`.
- Rechazo de `PICKUP` en schema y base; aceptacion de `SEA`, `AIR` y `LAND`.
- `COMPLETED` bloqueado sin BL `READY` o requisitos documentales verificados.
- Verificacion rechazada para documento ajeno, tipo distinto, no `READY` o invalidado.
- Documento de despacho deja cero enlaces en `shipment_documents`.
- Hash de archivo grande con lectura por chunks y reintento idempotente.
- ZIP grande con memoria acotada, nombres repetidos y objetos faltantes.
- Job de exportacion no permite polling ni descarga desde otra empresa.

## Orden de implementacion

1. Piezas, conteo y peso.
2. Busqueda y respuesta resumida.
3. `provided_by` y verificacion de requisitos.
4. Desvinculacion de documentos de despacho.
5. Pipeline asincrono de hash.
6. Exportaciones ZIP asincronas.
7. Integracion del selector de estados y del nuevo frontend.
