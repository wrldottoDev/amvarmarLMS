# ADR-0009: Límites de archivo por tipo de documento

- **Fecha:** 2026-08-19
- **Estado:** Aprobado
- **Aprobado por:** Otoniel González (revisión técnica) + AMVARMAR (decisión de negocio)

## Contexto

Hay que fijar tamaño máximo por tipo de documento (factura, BL, packing list, etc.) y decidir si se aceptan
archivos ZIP (riesgo de ZIP bomb, ver Paso 3.1) o se prohíben del todo.

## Decisión

**Límites iniciales:**

| Tipo | Tamaño máximo por archivo |
|---|---|
| PDF | 250 MB |
| JPG, JPEG, PNG, WebP, HEIC | 100 MB |
| Otros documentos permitidos | 100 MB |
| Carga total por lote | 1 GB |

**Configurables por `SUPER_ADMIN` sin tocar código** — se almacenan en `system_settings` (tabla ya existente
en el documento de arquitectura, sección 3.9), claves: `upload.max_size.pdf`, `upload.max_size.image`,
`upload.max_size.other`, `upload.max_size.batch`. Nuevo permiso `system_settings.manage`, exclusivo
`SUPER_ADMIN` (no `OPS_ADMIN`) — se agrega a ADR-0004.

**ZIP: prohibido en la subida, sin excepción.** Cierra el punto que había quedado abierto desde el Paso 0.4 y
la lista de pruebas de Paso 3.1 del plan ("ZIP bomb → rechazado o ZIP prohibido, según decisión del Paso 0.4")
— queda resuelto como prohibición total, no como detección de bomba. Simplifica la superficie de ataque: no
hay que descomprimir nada para validar.

**"Otros documentos permitidos" — whitelist explícita a definir**, la decisión no la enumera. Recomendación:
`DOCX`, `XLSX`, `CSV`, `TXT` — **excluyendo explícitamente** variantes con macros (`.docm`, `.xlsm`, `.pptm`)
del catálogo de extensiones permitidas desde el inicio. Es más simple y más seguro bloquear la extensión que
intentar parsear y despojar macros de un Office XML.

**Pipeline de procesamiento — orden de operaciones (fija un punto no especificado en la decisión):**

```
UPLOADING (transferencia por partes, multipart directo a storage)
  → validar MIME real + tamaño + checksum (Paso 3.1)
  → escaneo antivirus (Paso 3.2): scan_status PENDING → AVAILABLE | QUARANTINED
      si QUARANTINED → upload_status = FAILED, fin
  → si AVAILABLE: upload_status = PROCESSING (genera derivado optimizado)
  → upload_status = READY | FAILED
```

**Motivo de este orden, no explícito en la decisión original:** generar la versión optimizada (WebP/AVIF,
recompresión de PDF) implica correr librerías de procesamiento (Pillow, herramientas de PDF) sobre el
contenido del archivo. Ejecutar eso **antes** de que pase el antivirus expone esas librerías a contenido no
confiable — son superficie de ataque propia (decompression bombs de imagen, PDFs malformados). Escanear primero,
optimizar después.

**Tres estados distintos sobre el mismo documento — no se mezclan en un solo campo:**

| Campo | Valores | Capa |
|---|---|---|
| `upload_status` | `UPLOADING → PROCESSING → READY` / `FAILED` | Técnico (esta decisión) |
| `scan_status` | `PENDING → AVAILABLE` / `QUARANTINED` | Seguridad (Paso 3.2) |
| `requirement_status` | `PENDING → UPLOADED → VERIFIED` / `REJECTED` / `NOT_APPLICABLE` | Negocio (ADR-0003) |

La revisión de Operaciones (`requirement_status`) solo tiene sentido cuando `upload_status = READY` y
`scan_status = AVAILABLE` — no se puede verificar un documento que todavía no terminó de procesarse o que
está en cuarentena.

**Optimización:**
- Imágenes: derivado en WebP o AVIF para visualización.
- Escaneos: compresión sin pérdida o alta fidelidad (no se degradan como una foto normal).
- Se corrige orientación; se elimina metadata privada (GPS, etc.) **solo de la copia de visualización** — el
  original conserva su metadata completa, protegido igual que cualquier documento privado.
- PDF: puede optimizar recursos/imágenes duplicadas internamente.
- **PDF firmado digitalmente: nunca se modifica** — no se genera derivado, se sirve el original directo, para
  no invalidar la firma.
- El original nunca se sobrescribe ni se reemplaza; se conserva durante todo el período de retención
  (ADR-0007).

**Descarga en ZIP (bajo demanda, no subida):**
- Nuevo endpoint, ej. `POST /shipments/{id}/documents/zip` → job asíncrono (worker ya existe desde Paso 3.2)
  arma el ZIP con los **archivos originales** (no las versiones optimizadas), organizados por tipo de
  documento, en un prefijo temporal del storage.
- Devuelve enlace firmado de corta duración.
- El ZIP temporal se elimina tras la descarga o al vencer el enlace — sin copias permanentes. Implementación
  recomendada: `Lifecycle Rule` del bucket sobre el prefijo temporal (expiración automática a N horas) en vez
  de depender únicamente de un job de limpieza — doble seguro, el bucket borra igual aunque el job falle.

**Seguridad — reafirma y detalla lo ya establecido en Paso 3.1/3.2, sin cambios de diseño:**
- MIME real, no extensión ni `Content-Type` del cliente.
- Ejecutables, macros y archivos peligrosos bloqueados por whitelist de extensión (ver arriba), no por
  detección de contenido malicioso genérico.
- Antivirus (Paso 3.2), checksum SHA-256 (ya en Paso 3.1).
- **Storage cifrado en reposo** — requisito nuevo y explícito para la configuración del bucket (SSE-S3 o
  SSE-KMS), no estaba anotado como checklist de infraestructura hasta ahora.
- Descarga siempre autenticada + autorizada por empresa propietaria — ya establecido (Paso 3.1, aislamiento
  por empresa).
- **Multipart directo a storage, nunca buffer completo en memoria de FastAPI** — confirma y refuerza el diseño
  de "flujo en dos tiempos" de Paso 3.1: para archivos grandes, el "presign" se convierte en inicio de
  multipart upload de S3 (`CreateMultipartUpload` + URLs firmadas por parte), no un único `PUT` firmado.

## Pendiente de precisar

1. ~~**Whitelist exacta de "otros documentos permitidos"**~~ — **Resuelto.** Ver "Formatos por tipo de
   documento" abajo.
2. **Alcance de "lote" (1 GB total):** queda como sesión de subida (ej. todos los archivos que el usuario
   selecciona de una vez para un shipment), no un acumulado histórico por carga — asunción de bajo riesgo,
   sin objeción; se puede ajustar sin costo cuando se implemente Paso 3.1, es solo un límite de validación.

## Formatos permitidos por tipo de documento — resuelto

La whitelist **no es global** — depende de qué `document_type` (catálogo de ADR-0003) se está subiendo. El
cliente elige el tipo de documento antes de subir el archivo (ya es parte del flujo de presign de Paso 3.1:
el `document_type` va en la solicitud de presign, así que el backend ya sabe qué tipo es antes de recibir
el archivo).

- **DOCX → se convierte a PDF automáticamente.** Se acepta como formato de entrada para cualquier tipo de
  documento, pero el sistema lo convierte a PDF como parte del paso `PROCESSING` (mismo lugar del pipeline
  donde se generan los derivados optimizados de imagen). El DOCX original se conserva igual que cualquier
  original (nunca se sobrescribe), pero el PDF convertido es el que se usa para visualización, verificación
  y descarga individual — el ZIP completo sigue incluyendo el original DOCX, no el PDF convertido, porque
  el ZIP entrega originales (ver "Descarga en ZIP" arriba).
- **XLSX, CSV, TXT → solo para tipos de documento donde tiene sentido**, no en general. Se especifican por
  tipo antes de la subida (el `document_type` determina qué formatos acepta esa subida en particular):

  | Tipo de documento (ADR-0003) | Formatos aceptados |
  |---|---|
  | Factura comercial | PDF, JPG/JPEG/PNG/WebP/HEIC, DOCX (→PDF) |
  | SLI | PDF, JPG/JPEG/PNG/WebP/HEIC, DOCX (→PDF) |
  | Packing list | PDF, JPG/JPEG/PNG/WebP/HEIC, DOCX (→PDF), **XLSX, CSV** |
  | BL | PDF, JPG/JPEG/PNG/WebP/HEIC |
  | Permiso especial | PDF, JPG/JPEG/PNG/WebP/HEIC |
  | Prueba de entrega (ADR-0006) | PDF, JPG/JPEG/PNG/WebP/HEIC |

  `TXT` queda disponible como formato genérico de bajo riesgo (texto plano, sin macros ni contenido activo
  posible) pero no se le asigna un tipo de documento específico por ahora — se puede habilitar por tipo más
  adelante sin cambio de esquema, solo agregando la fila a esta tabla. **Confirmado: Packing list es el único
  tipo que acepta XLSX/CSV.** Factura, SLI, BL, Permiso especial y Prueba de entrega quedan solo en
  PDF/imagen/DOCX→PDF.
- Esto agrega una columna a `document_types` (catálogo de ADR-0003): `allowed_formats` (array o tabla
  relacionada `document_type_formats`). El endpoint de presign (Paso 3.1) valida el formato solicitado contra
  esta lista específica del `document_type`, no contra una whitelist global de la aplicación.

## Alternativas consideradas

- Permitir ZIP con detección de bomba (límite de ratio de descompresión, límite de archivos internos).
  Descartada: la decisión prohíbe ZIP directamente, más simple y sin superficie de ataque de descompresión.
- Optimizar antes de escanear (procesar primero, revisar después). Descartada por el riesgo de seguridad ya
  explicado — el orden correcto es escanear primero.

## Consecuencias

- Paso 3.1 se actualiza: el "presign" para archivos por encima de un umbral (recomendado: mismo umbral mínimo
  de parte de S3 Multipart Upload, 5 MB) se convierte en flujo de multipart upload real (`CreateMultipartUpload`
  → URLs firmadas por parte → `CompleteMultipartUpload`), no un único PUT firmado como estaba planteado
  originalmente para el caso simple.
- `documents` necesita 3 columnas de estado en vez de 1 (`upload_status`, y ya existían implícitamente
  `scan_status` de Paso 3.2 y `requirement_status` de ADR-0003) — se deja explícito para que Paso 2.2/3.1 no
  colapsen las tres en un solo campo.
- Nuevo endpoint `POST /shipments/{id}/documents/zip` + worker de generación de ZIP — se agrega al alcance de
  Fase 3 (después de Paso 3.2, reutiliza el worker ya construido ahí).
- `system_settings` se siembra con las 4 claves de límites desde Fase 1 (Paso 1.4, junto con el resto de seeds),
  aunque la subida de archivos no se construye hasta Fase 3 — evita otra migración retroactiva.
- Nuevo permiso `shipments`-adyacente `system_settings.manage`, exclusivo `SUPER_ADMIN` — se agrega a ADR-0004.
- Checklist de infraestructura de Paso 3.1 gana un ítem explícito: cifrado en reposo del bucket (SSE-S3/KMS).
- Whitelist de extensiones (macros excluidas) reemplaza cualquier intento de detección de macros por contenido
  — más barato y más confiable.
