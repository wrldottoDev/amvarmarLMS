# ADR-0007: Retención de documentos y auditoría

- **Fecha:** 2026-08-19
- **Estado:** Aprobado
- **Aprobado por:** Otoniel González (revisión técnica) + AMVARMAR (decisión de negocio)

## Contexto

El documento de arquitectura propone 2–5 años como rango inicial para auditoría. Falta confirmar el número
exacto y si documentos y auditoría tienen la misma retención o distinta. También si hay obligación legal/fiscal
que fije un mínimo.

## Decisión

**Actualizado tras revisión:** no se elimina físicamente ningún documento. A los 6 meses se **recomprime al
máximo posible** y se mueve a la sección de historial, junto con la carga completa. El documento sigue
existiendo y sigue siendo descargable — solo que en su versión archivada, más liviana. Reemplaza la versión
anterior de esta decisión, que sí contemplaba purga física.

**Retención operativa: 6 meses desde `DELIVERED` o `CANCELLED`.** No se elimina la carga — se archiva. La
carga sigue existiendo, deja de participar en la operación activa.

**`ARCHIVED` no es un estado logístico nuevo** — no se agrega al catálogo de `shipments.status` de ADR-0001.
Es una condición interna independiente (`archived_at IS NOT NULL`), igual que "faltan documentos" no reemplaza
el estado logístico (principio ya establecido en la sección 0 del documento de arquitectura).

**Campos nuevos en `shipments`:**

| Campo | Tipo | Descripción |
|---|---|---|
| `retention_until` | `TIMESTAMPTZ`, nullable | Se calcula y graba en la misma transacción que la transición a `DELIVERED`/`CANCELLED` (Paso 2.4): `occurred_at + 6 meses`. Se materializa como columna — no se calcula al vuelo — para que el worker de archivado (Fase 4) pueda hacer `WHERE retention_until <= now()` de forma barata. |
| `archived_at` | `TIMESTAMPTZ`, nullable | Se llena cuando corre el archivado. `NULL` = activo. |
| `legal_hold` | `BOOLEAN`, default `false` | Bloquea archivado/eliminación mientras esté activo. |
| `legal_hold_reason` | `TEXT`, nullable | Obligatorio si `legal_hold = true`. Cubre tanto retención especial de Operaciones como obligación legal/administrativa — un solo mecanismo, el motivo distingue el caso. |

**Qué pasa al archivar (job automático, ver "Consecuencias" para el mecanismo):**
- Deja de aparecer en dashboards, alertas, pendientes y consultas operativas — toda query operativa de Fase 2
  (Paso 2.5) filtra `archived_at IS NULL` desde el día en que se construya, no como parche posterior.
- Queda disponible solo en `Historial de despachos` (sección exclusiva del cliente, ver abajo) — de solo lectura.
- Se conservan del expediente: código de carga, factura, origen, destino, fechas, estado final, WR (si aplica),
  referencias. El resto de la ficha operativa deja de ser relevante pero no se borra — la fila de `shipments`
  no se trunca, la vista archivada solo *muestra* un subconjunto de campos.
- **Los archivos se recomprimen al máximo posible, nunca se eliminan.** Dos caminos según el tipo de
  documento, por el conflicto con la regla de ADR-0009 de que un PDF firmado nunca se modifica:
  - **Documentos sin firma digital** (la mayoría — facturas, packing list, BL, permisos, prueba de entrega,
    imágenes): se regenera el archivo con compresión agresiva orientada a archivo histórico, no a calidad de
    uso diario — parámetros más agresivos que la optimización de visualización de ADR-0009, porque a partir de
    los 6 meses el valor es de registro/consulta esporádica, no de operación activa. La versión comprimida
    **reemplaza** al original en el storage — es la única situación del proyecto donde un original se
    reemplaza, justificada explícitamente por ahorro de espacio en archivo histórico, no por optimización de
    visualización.
  - **PDF firmados digitalmente:** no se recomprime el contenido — modificar un solo byte invalida la firma.
    Se aplica únicamente **compresión de contenedor sin pérdida** (gzip/zstd sobre el objeto en storage), que
    no altera el PDF en sí — se descomprime de forma transparente al servir la descarga, el archivo que llega
    al usuario es bit-idéntico al original firmado.
  - La fila de `documents` permanece igual (nunca se borra), se agregan `archived_compressed_at` (cuándo corrió
    la recompresión) y `original_size_bytes` junto al tamaño actual, para trazabilidad de cuánto se redujo.
  - Descarga de un documento archivado sigue respondiendo `200 OK` con el archivo — no hay `410 Gone`, porque
    nada desaparece.

**Excepciones — no se archiva ni purga mientras:**
- La carga sigue abierta (no llegó a `DELIVERED`/`CANCELLED` — chequeo defensivo, `retention_until` ni existe
  todavía en ese caso).
- Existe una `delivery_dispute` en estado `OPEN` (ADR-0006) — una disputa activa bloquea el archivado aunque
  ya pasaron los 6 meses.
- `shipment.legacy_review_required = true` (ADR-0002/ADR-0005) — no se archiva algo que todavía no se terminó
  de clasificar correctamente.
- `legal_hold = true`.

El job de archivado consulta: `status IN (DELIVERED, CANCELLED) AND retention_until <= now() AND archived_at IS
NULL AND legal_hold = false AND legacy_review_required = false AND NOT EXISTS (delivery_disputes OPEN)`.

**Historial de despachos (cliente) — funcionalidad nueva de Fase F:**

Filtros: código de carga, número de factura, WR (cuando aplique), origen/destino, rango de fechas, estado
final. Se implementa reutilizando `GET /shipments` con un parámetro `archived=true` y los mismos filtros que
ya existen, en vez de crear un endpoint separado — es la misma entidad, distinta vista, no un recurso nuevo.
Solo lectura: sin `PATCH`, sin transiciones, sin subida de documentos sobre un shipment archivado.

**Bitácora de auditoría: 2 años, independiente de la retención de documentos.** `audit_logs` no se purga junto
con los documentos del expediente — sigue su propio ciclo. Confirma el rango que ya proponía el documento de
arquitectura (2–5 años), fijado en el extremo inferior.

**Archivado seguro (ya no es "eliminación"):**
- Aviso al cliente antes de la recompresión (nueva entrada de notificación para ADR-0008 — ej. "tus documentos
  de la carga SHP-... se archivarán en N días; si necesitás la versión de máxima calidad, descargala antes").
  El aviso ahora es sobre pérdida de calidad, no sobre pérdida de acceso.
- Descarga disponible siempre, antes y después del archivado — mismo endpoint de descarga existente
  (Paso 3.1), sin cambios en el contrato de la API.
- Tarea automática ejecuta el archivado + recompresión (ver Consecuencias — requiere Celery beat, no es outbox
  reactivo).
- Toda recompresión queda en `audit_logs`: qué documento, de qué shipment, cuándo, tamaño antes/después,
  ejecutado por el job (actor = sistema, no un usuario).

## Alternativas consideradas

- Legal hold por defecto en documentos de factura/aduana, purga como excepción manual. Descartada tras
  confirmar con AMVARMAR que 6 meses ya está validado legalmente — hubiera sido más conservador pero
  innecesario dado que la validación ya se hizo.
- Purgar también la fila de `documents`, no solo el archivo físico. Descartada: pierde la capacidad de
  responder "¿qué documentos tuvo esta carga?" incluso después de vencida la retención.
- Purga física completa del archivo a los 6 meses (versión original de esta decisión). Reemplazada: se prefiere
  recomprimir y conservar indefinidamente — más seguro frente a obligaciones legales/fiscales que puedan
  aparecer más adelante, y evita la pérdida irreversible de evidencia documental.
- Recomprimir también los PDF firmados a nivel de contenido. Descartada: invalidaría la firma digital,
  contradice la regla ya establecida en ADR-0009. Se usa compresión de contenedor sin pérdida en su lugar.

## Consecuencias

- **Nuevo mecanismo de ejecución en Fase 4:** el archivado/recompresión es un barrido periódico (ej. diario,
  Celery beat con `crontab`), distinto del patrón reactivo del transactional outbox (Paso 4.1, que procesa
  eventos a medida que ocurren). Es la primera tarea programada por calendario del proyecto — se agrega
  explícitamente al alcance de Fase 4, no estaba en el documento de arquitectura original.
- Paso 2.2 (`shipments`) agrega las 4 columnas de retención desde el diseño inicial del esquema, aunque el job
  que las usa no se construye hasta Fase 4 — así Fase 2 no necesita una migración retroactiva.
- Paso 2.4 (motor de transiciones) calcula y graba `retention_until` en la misma transacción que la transición
  a `DELIVERED`/`CANCELLED`.
- Paso 2.5 (dashboard/listados) filtra `archived_at IS NULL` en toda consulta operativa desde el diseño
  inicial, no como ajuste posterior en Fase 4.
- Paso 3.1/3.2 gana un segundo pipeline de compresión (archival, más agresivo) además del de visualización
  ya definido en ADR-0009 — y una rama condicional según `documents.is_digitally_signed` para decidir entre
  recompresión de contenido o compresión de contenedor sin pérdida.
- No hay `410 Gone` en el catálogo de códigos HTTP — se descarta ese ajuste al Apéndice C del plan, ya no
  aplica.
- Fase F suma "Historial de despachos" como vista nueva (candidata a paso F.9), reutilizando el endpoint de
  listado existente con filtro `archived=true`.
- ADR-0008 debe incluir el evento "documentos por archivar" (antes "por vencer" — cambia el tono del aviso,
  ya no se pierde el documento, se pierde calidad) como entrada obligatoria del catálogo.
- `legal_hold`/`legal_hold_reason` requieren un permiso propio para activarlos/desactivarlos (candidato:
  `shipments.legal_hold.manage`, análogo a los códigos ya definidos en ADR-0004 — pendiente de agregar ahí
  cuando se revise ese ADR, o directamente en el seed de Fase 1 si ADR-0004 ya quedó cerrado).
