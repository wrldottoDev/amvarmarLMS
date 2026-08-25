# ADR-0005: Regla de Miami para referencias tipo WR

- **Fecha:** 2026-08-19
- **Estado:** Aprobado
- **Aprobado por:** Otoniel González (revisión técnica) + AMVARMAR (decisión de negocio)

## Contexto

El documento de arquitectura exige que `shipment_references(type=WR)` solo se permita cuando el punto
operativo/origen corresponde a Miami. El legacy no modela este dato explícitamente. Hay que decidir si se
infiere de `shipper`, de `carrier`, o si se captura como campo nuevo explícito en el shipment.

## Decisión

**No se infiere origen de texto libre.** Se crea un catálogo estructurado de ubicaciones y bodegas; la regla de
WR se ata a un flag de bodega, nunca a una comparación de string como `city == 'MIA'`.

**Esquema:**

`locations`:

| Campo | Ejemplo |
|---|---|
| `country_code` | `US`, `CN`, `CR` |
| `city_code` | `MIA`, `SHA`, `SJO` |
| `location_code` | `US-MIA`, `CN-SHA`, `CR-SJO` (país + ciudad, evita ambigüedad) |
| `name` | Miami, Shanghái, San José |

`facilities` (bodegas/instalaciones de AMVARMAR — FK a `locations`):

| Campo | Descripción |
|---|---|
| `location_id` | FK a `locations` |
| `facility_code` | ej. `MIA-WH-01` |
| `facility_type` | ej. `WAREHOUSE` |
| `uses_warehouse_receipt` | `true` únicamente para la bodega de Miami hoy |

`shipments` agrega:
- `origin_location_id` — **obligatorio** en toda carga (de dónde viene, tenga bodega o no).
- `origin_facility_id` — **opcional**, solo aplica si AMVARMAR tiene bodega física en esa ubicación.

**Regla de WR (reemplaza la redacción original del documento de arquitectura, sección 3.5/4.5):**

> `shipment_references(type=WR)` solo se permite si `shipment.origin_facility.uses_warehouse_receipt = true`.

No se compara ciudad ni país directamente — abrir una bodega nueva en el futuro es activar un flag, no tocar
código ni políticas.

**Reglas de negocio:**
- Hoy, solo Miami tiene bodega (`uses_warehouse_receipt = true`); todas las demás ubicaciones no tienen bodega,
  por lo tanto no usan WR.
- Cargas fuera de Miami se identifican por factura, referencia de proveedor, BL u otros — nunca por WR.
- El código interno (`SHP-2026-001284`) sigue siendo el identificador principal de toda carga, WR o no.
- Operaciones registra el WR cuando la mercancía se recibe físicamente en la bodega de Miami.
- **Dirección inversa de la regla, nueva respecto al documento original:** una carga cuyo `origin_facility`
  tiene `uses_warehouse_receipt = true` **debe** tener al menos un WR antes de considerarse completamente
  `STORED`/lista para `DISPATCH_REQUESTED`. Antes la regla solo prohibía WR fuera de Miami; ahora también
  **exige** WR dentro de Miami. Se implementa como validación en la transición `RECEIVED → STORED` (Paso 2.4):
  si `uses_warehouse_receipt = true` y no existe referencia `WR`, la transición se rechaza con `409` y código
  de error específico (ej. `SHIPMENT_MISSING_WR`).
- Para ubicaciones sin bodega, el campo WR no se solicita al usuario — la interfaz lo omite directamente
  cuando `origin_facility_id` es nulo o `uses_warehouse_receipt = false`.
- **Unicidad de WR:** única dentro de la bodega, no globalmente en todo el sistema. Como hoy solo existe una
  bodega, el efecto práctico es el mismo, pero el modelo debe soportar múltiples bodegas desde el diseño.
  Implementación: `shipment_references` no tiene columna de bodega directamente (es genérica por tipo/valor);
  para un índice único parcial correcto se necesita denormalizar `facility_id` en la fila de referencia
  cuando `type = WR`, o resolver la unicidad con una restricción a nivel de servicio dentro de la transacción
  de creación (`SELECT ... FOR UPDATE` sobre las referencias `WR` de la misma bodega antes de insertar).

## Migración legacy — decisión confirmada con AMVARMAR

**Hallazgo del inventario (Paso 0.3):** `core_warehouse` no tiene ningún campo estructurado de ubicación —
ni ciudad, ni país. Los únicos campos relacionados (`shipper`, `carrier`) son texto libre, y esta decisión
prohíbe explícitamente inferir origen de ellos.

**Confirmado con AMVARMAR:** durante todo el período que cubre esta base de datos legacy, AMVARMAR operó
**únicamente** desde la bodega de Miami. No hay evidencia de cargas de otro origen mezcladas sin distinguir.

**Regla de migración (Paso 5.2):**
- Las 584 filas de `core_warehouse` se asignan a `origin_location_id = US-MIA` y
  `origin_facility_id = <bodega Miami>` **por conocimiento operativo confirmado por AMVARMAR, no por inferencia
  de `shipper`/`carrier`** — la distinción importa para la auditoría de la migración: el origen no se dedujo
  de un campo de texto, se declaró como hecho conocido del negocio antes de migrar.
- Los `wr_number` migrados se mantienen como referencias `WR` reales (no como `legacy_review_required`),
  porque con el origen confirmado, la regla de negocio (Miami usa WR) se cumple para el 100% de los casos.
- Si en el futuro aparece evidencia de que algún registro específico no era de Miami, se corrige manualmente
  con `legacy_review_required = true` puntual — no bloquea el resto de la migración.
- Ningún WR existente se elimina ni se reasigna a una ubicación inventada.
- Los 2 WR con formato inconsistente detectados en el inventario (`WR105921.`, `WR2308|`) se limpian en el
  Paso 5.1 (limpieza en origen) antes de migrar — no es un problema de esta decisión, es higiene de datos.

## Alternativas consideradas

- Inferir origen de `shipper`/`carrier` con texto libre. Descartada explícitamente por la decisión — datos no
  confiables, alta probabilidad de asignar ubicaciones incorrectas.
- Dejar los 584 registros con `legacy_review_required = true` por defecto y revisar uno por uno. Descartada
  tras confirmar con AMVARMAR que el origen histórico es 100% Miami — hubiera sido trabajo manual innecesario
  sobre un hecho ya conocido.

## Consecuencias

- El seed inicial de `locations`/`facilities` (parte de Fase 1 o inicio de Fase 2) debe incluir al menos
  `US-MIA` con `uses_warehouse_receipt = true` antes de que el migrador de Fase 5 pueda correr.
- Paso 2.2 (`shipments`) agrega `origin_location_id` (obligatorio) y `origin_facility_id` (opcional) al modelo,
  no contemplados en el esquema original del documento de arquitectura.
- Paso 2.3 (`shipment_references`) implementa la regla de aplicabilidad de WR contra
  `origin_facility.uses_warehouse_receipt`, no contra un valor de texto.
- Paso 2.4 (motor de transiciones) agrega una validación nueva: `RECEIVED → STORED` se bloquea si la bodega de
  origen exige WR y no existe ninguna referencia `WR` en el shipment.
- Paso 5.2 (migrador) trata el 100% de `core_warehouse` como origen Miami confirmado — no genera
  `legacy_review_required` masivo para este campo específico.
