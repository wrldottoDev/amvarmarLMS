# ADR-0003: Catálogo de tipos de documento

- **Fecha:** 2026-08-19
- **Estado:** Aprobado
- **Aprobado por:** Otoniel González (revisión técnica) + AMVARMAR (decisión de negocio)

## Contexto

`document_types` necesita un catálogo cerrado: factura comercial, packing list, BL, permiso de importación, etc.
Hay que decidir cuáles son obligatorios y en qué estado del shipment se exigen (ej. factura obligatoria antes
de `DISPATCH_REQUESTED`).

## Decisión

**Catálogo inicial de `document_types`:**

| Documento | Quién lo proporciona | Aplicabilidad | Momento límite |
|---|---|---|---|
| Factura comercial | Cliente o proveedor | Obligatoria para la carga. Si el proveedor la entrega directo a AMVARMAR, deja de ser acción pendiente del cliente. | Antes de `DISPATCHED` |
| SLI | Cliente o proveedor | Obligatoria únicamente para cargas originadas en Miami (aplicabilidad **automática**, ligada a ADR-0005). Si el proveedor la entrega directo, deja de ser acción pendiente del cliente. | Antes de `DISPATCHED` |
| Packing list | Proveedor; lo carga Operaciones | Requerido cuando el proveedor lo emita. Visible para el cliente. | Antes de `DISPATCHED` |
| BL | Operaciones/administrador | Se genera y carga después del despacho para consulta del cliente. | Después de `DISPATCHED` |
| Permiso especial | Cliente | Obligatorio únicamente si la mercancía requiere inspección/autorización especial (aplicabilidad **heurística + revisión manual**, ver flujo abajo). | Antes de `DISPATCHED` |
| Prueba de entrega (`PROOF_OF_DELIVERY`) | Operaciones | Obligatoria para completar la transición `DISPATCHED → DELIVERED` (ver ADR-0006). | En el momento de registrar la entrega |

**Reglas generales:**
- El sistema distingue entre *documento obligatorio para la carga* (propiedad del requisito) y *documento
  pendiente del cliente* (quién debe actuar ahora — puede ser el cliente, el proveedor a través de Operaciones,
  o nadie si ya está resuelto).
- Si factura comercial o SLI las entrega el proveedor directamente, Operaciones las carga y el cliente deja de
  verlas como pendientes — el requisito se satisface igual, cambia quién lo resolvió.
- Una carga no puede pasar a `DISPATCHED` si le falta un documento obligatorio aplicable (validación en la
  transición `PREPARING → DISPATCHED`, Paso 2.4, y también en `dispatch_requests.approve/complete`, Paso 3.4 —
  es el mismo momento visto desde dos entidades).
- Operaciones debe verificar (`VERIFIED`) los documentos antes del despacho — **subir el archivo no basta**,
  `UPLOADED` por sí solo no satisface el requisito.
- Todo cambio de aplicabilidad, rechazo o aprobación queda en auditoría (`audit_logs` + evento en
  `shipment_events` si afecta la carga).

**Estados del requisito documental — extiende el estado genérico de `shipment_requirements`:**

`PENDING → UPLOADED → VERIFIED` (satisface el requisito) **o** `→ REJECTED` (vuelve a aceptar nueva subida,
reinicia el ciclo desde `UPLOADED`) **o** `→ NOT_APPLICABLE` (nunca aplicó, no es una excepción).

**Nota de diseño — desviación del documento de arquitectura original:** `shipment_requirements` se definió ahí
con 3 estados genéricos (`OPEN`, `FULFILLED`, `WAIVED`) válidos para cualquier tipo de requisito, no solo
documentos. Esta decisión amplía el estado a 5 valores específicos **cuando `requirement_type = DOCUMENT`**:

- `NOT_APPLICABLE` **no es lo mismo que** `WAIVED`. `WAIVED` (del diseño original) sigue reservado para cuando
  Operaciones exonera manualmente un requisito que sí aplicaba — exige permiso específico y motivo obligatorio
  (Paso 2.4). `NOT_APPLICABLE` significa que el requisito nunca aplicó a esta carga (ej. SLI en carga que no es
  de Miami) — no es una excepción, es una regla. Mezclarlos pierde la distinción en auditoría entre "se saltó
  algo obligatorio" y "esto nunca fue obligatorio aquí".
- Requisitos no documentales (si existen) siguen usando `OPEN`/`FULFILLED`/`WAIVED` sin cambios.

**Filtro de permiso especial — flujo heurístico, no decisión automática:**

1. Durante la prealerta, el sistema hace preguntas sobre el tipo de mercancía.
2. Si detecta posible mercancía regulada, marca `shipment.permit_review_required = true` (flag preliminar en
   el shipment, **antes** de que exista una fila de requisito firme).
3. Operaciones revisa el caso.
4. Operaciones determina `REQUIRED` o `NOT_APPLICABLE` — recién ahí se crea/confirma la fila en
   `shipment_requirements` con `requirement_type = DOCUMENT`, `document_type = PERMISO_ESPECIAL`.
5. Si `REQUIRED`, la carga no puede despachar hasta que el permiso esté cargado y `VERIFIED`.

El cliente no puede descartar por sí solo una advertencia de permiso — la determinación final es exclusiva de
Operaciones.

## Pendiente de precisar (asunciones tomadas, confirmar o corregir)

1. **Detección de aplicabilidad del packing list:** no se describió un mecanismo de detección (a diferencia del
   permiso especial, que tiene cuestionario de prealerta). Se asumió: arranca en `PENDING` por defecto:
   Operaciones lo pasa a `NOT_APPLICABLE` si confirma que el proveedor no lo emite en ese caso.
2. **Ciclo de `REJECTED`:** se asumió que no tiene límite de reintentos — el cliente/proveedor puede volver a
   subir indefinidamente hasta que Operaciones verifique. Si se necesita un tope, es un requisito nuevo.
3. ~~**BL y `DELIVERED`**~~ — **Resuelto en ADR-0006.** El BL no bloquea `DELIVERED`. Los únicos requisitos para
   esa transición son los cuatro de ADR-0006 (fecha/hora, prueba de entrega, receptor, actor de Operaciones).
   Se agrega el sexto tipo de documento `PROOF_OF_DELIVERY` a la tabla de arriba.

## Alternativas consideradas

- Usar los 3 estados genéricos (`OPEN`/`FULFILLED`/`WAIVED`) también para documentos, sin los 5 estados
  específicos. Descartada: pierde la distinción operativa entre "subido pero no revisado" y "verificado", que
  es exactamente lo que impide que un archivo basura cuente como requisito satisfecho.

## Consecuencias

- `shipment_requirements` necesita un campo de estado más ancho para `requirement_type = DOCUMENT` que para
  otros tipos de requisito — el modelo de datos de Fase 2 (Paso 2.4) y Fase 3 (Paso 3.1, 3.4) debe reflejar
  esta extensión, no solo los 3 estados genéricos del documento de arquitectura original.
- `shipments` necesita el campo `permit_review_required BOOLEAN` como flag preliminar, independiente de la fila
  de requisito formal que se crea después de la revisión de Operaciones.
- El dashboard del cliente (Paso 2.5) debe distinguir "documentos pendientes del cliente" de "documentos
  pendientes de Operaciones" — `open_requirements_count` tal como está definido en el documento de arquitectura
  no separa por actor responsable; hace falta un campo adicional (ej. `pending_actor`) o un conteo separado
  para no mostrarle al cliente pendientes que en realidad debe resolver Operaciones (packing list, BL).
