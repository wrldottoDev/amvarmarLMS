# Propuesta frontend Next.js

## Objetivo de experiencia

La interfaz se rediseña como una herramienta operativa, no como una pagina promocional. La persona
debe poder encontrar una carga, entender su estado, corregirla, adjuntar documentos o iniciar un
despacho sin conocer la estructura interna del sistema.

La autenticacion actual no cambia. Se reutilizan `contexto-sesion`, el cliente OpenAPI, el refresh
mutex, React Query y las rutas protegidas existentes.

## Principios de interfaz

- La primera pantalla es el trabajo operativo pendiente.
- Cada vista tiene una accion primaria clara.
- Los controles disponibles dependen del rol y del estado real del recurso.
- La tabla prioriza lectura y accion repetitiva en escritorio; movil usa filas resumidas.
- Las acciones frecuentes se resuelven en contexto, sin obligar a abrir el detalle.
- Los formularios muestran primero los campos que cambian reglas posteriores.
- Los mensajes explican el problema y el campo a corregir, no detalles internos de la API.
- Los estados de carga y los requisitos documentales se presentan por separado.
- Se usan iconos Lucide, controles familiares y tooltips para acciones solo con icono.
- Las secciones son bandas sin marcos decorativos innecesarios; las tarjetas se reservan para
  elementos repetidos, modales y herramientas realmente contenidas.

## Navegacion

### Operaciones

```text
Operaciones
├── Cargas
├── Despachos
├── Documentos pendientes
├── Empresas
├── Usuarios
└── Auditoria
```

### Cliente

```text
Inicio
├── Mis cargas
├── Solicitar despacho
├── Mis despachos
├── Avisos
└── Cuenta
```

El shell conserva navegacion lateral en escritorio y una navegacion compacta en movil. Las
opciones se derivan de permisos efectivos; no se usan comprobaciones dispersas de nombres de rol.
`CLIENT_USER` y `CLIENT_ADMIN` muestran las mismas acciones dentro de su empresa, incluida la
gestion de prealertas, usuarios, documentos permitidos y solicitudes de despacho.

## Pantalla principal de cargas

Ruta: `/shipments`

La cabecera contiene titulo, cantidad de resultados y `Nueva carga` para quien tenga permiso. Debajo
aparece una barra de busqueda persistente y un boton de filtros. Los filtros activos se muestran como
chips removibles, de modo que siempre sea visible por que una fila aparece o no.

### Busqueda

La caja global busca simultaneamente:

- Numero SHP.
- Numero WR.
- Shipper.
- Carrier.
- Factura, PO, tracking, contenedor y referencias adicionales.

El panel expandible permite combinar campos especificos, empresa, estados y rango ETA. Los filtros
se guardan en `URLSearchParams`, por lo que recargar, volver atras o compartir la URL conserva la
vista. La busqueda global usa debounce; aplicar filtros explicitos es inmediato.

### Tabla de escritorio

Columnas base:

| Columna | Comportamiento |
| --- | --- |
| Identificador | WR o factura visible; SHP secundario |
| Empresa | Visible para Operaciones |
| Shipper | Texto truncado con valor completo en tooltip |
| Carrier | Igual que shipper |
| Ruta | Codigo origen a codigo destino |
| Piezas | Suma de cantidades |
| Estado | Control interactivo para quien pueda transicionar |
| Pendientes | Contador separado del estado logistico |
| ETA | Fecha y marca visual solo cuando requiere atencion |
| Acciones | Abrir, documentos y menu contextual con iconos |

Se conserva el selector de columnas actual. Anchos, columnas fijas y botones tienen dimensiones
estables para que cargar datos o abrir controles no mueva la tabla.

### Estado en linea

El badge de estado funciona como boton cuando el actor tiene permiso:

1. Al abrirlo consulta `transitions/available`.
2. Presenta solo los destinos validos.
3. Una transicion normal se confirma en el mismo popover.
4. Un retroceso o cancelacion abre un modal breve con motivo obligatorio.
5. Durante la solicitud, bloquea solo esa fila.
6. Al completar, actualiza la fila y los contadores mediante React Query.
7. Un conflicto `409` refresca la fila y muestra que otra persona la modifico.

En movil, la misma accion vive en el menu contextual de la carga para evitar comprimir el badge.

## Alta de carga

Ruta: `/cargas/nueva`

El flujo permanece en una sola pagina con secciones consecutivas y un pie de acciones estable.
No se convierte en un wizard largo que oculte informacion ya escrita.

### Orden del formulario

1. Origen operativo: bodega con WR u otro origen.
2. Empresa y ruta.
3. Identificadores comerciales.
4. Estado inicial.
5. Peso y volumen.
6. Piezas.
7. Datos complementarios.

La primera respuesta configura automaticamente facility, obligatoriedad del WR y factura. Las
validaciones se muestran junto al control afectado y el boton `Crear carga` permanece deshabilitado
mientras falte una regla obligatoria.

### Editor de peso

Componente: `EditorPeso`.

- Presenta kg y lb lado a lado.
- Al escribir kg, lb se recalcula inmediatamente.
- Al escribir lb, kg se recalcula inmediatamente.
- El ultimo campo editado queda marcado como fuente y el otro como calculado.
- Se evita la retroalimentacion circular conservando `lastEditedUnit`.
- Al salir del campo se normalizan tres decimales.
- El frontend envia un solo `PesoInput`; la API vuelve a convertir y validar.
- Cero, negativos, infinito y texto incompleto no pueden enviarse.

### Editor de piezas

Componente: `EditorPiezas`.

- El formulario nace con una fila: tipo `PALLET`, cantidad `1`.
- Cada fila permite tipo, cantidad, descripcion, peso y dimensiones.
- Agregar y quitar usa botones con iconos `Plus` y `Trash2`.
- El boton para quitar se deshabilita cuando solo queda una pieza.
- La cabecera muestra el total de unidades en tiempo real.
- Errores de una fila permanecen junto a esa fila.
- El orden visual se conserva al editar para no confundir cantidades.

Al crear correctamente, la misma experiencia avanza a `/cargas/{id}/archivos`. La cabecera conserva
el identificador recien creado y ofrece `Terminar` sin obligar a subir un archivo.

## Edicion de carga

Ruta: `/cargas/{id}/editar`

La edicion reutiliza `EditorPeso` y `EditorPiezas`, en lugar de mantener formularios distintos para
alta y correccion. El payload de piezas se envia al endpoint atomico de reemplazo.

El estado no se mezcla con el formulario descriptivo. Permanece disponible en la cabecera mediante
el mismo control de transiciones usado en el listado. Esto evita que guardar un cambio de shipper
modifique accidentalmente el estado.

Si cambia `row_version`, la pantalla no sobrescribe datos. Muestra los campos que quedaron obsoletos,
recarga la version reciente y permite volver a aplicar la correccion.

## Detalle de carga

Ruta: `/shipments/{id}`

La cabecera fija muestra identificador comercial, SHP, empresa, estado, pendientes y acciones. El
contenido se organiza en tres tabs:

| Tab | Contenido |
| --- | --- |
| Resumen | Ruta, pesos, piezas, datos comerciales e hitos |
| Documentos | Expediente, requisitos y subida |
| Historial | Eventos logisticos y correcciones auditables |

En movil los tabs ocupan todo el ancho y la accion primaria permanece accesible sin tapar contenido.

## Panel de archivos por carga

El componente actual `Expediente` se conserva conceptualmente y se rediseña como una herramienta
de trabajo con dos grupos:

- `Requiere atencion`: documentos pendientes, rechazados o subidos por verificar.
- `Expediente`: documentos disponibles, ordenados por tipo y fecha.

El selector de tipo consume el catalogo filtrado por contexto y permisos. Un cliente nunca ve tipos
`STAFF`; ocultarlos mejora UX, mientras el backend mantiene la validacion definitiva.
Cuando un tipo admite mas de un emisor, la subida solicita si el documento fue emitido por proveedor,
cliente, AMVARMAR, carrier o autoridad, limitado por el catalogo recibido.

Cada subida muestra progreso directo a S3 y despues estado `Procesando`. La UI consulta el documento
hasta `READY` o `FAILED`. Cerrar la pagina no cancela el worker. Reintentar crea una nueva reserva y
no reutiliza un documento fallido.

La descarga masiva crea un trabajo en segundo plano. El boton se transforma en indicador de progreso
y habilita `Descargar ZIP` cuando el job termina. No se mantiene una peticion HTTP abierta.

## Despachos

La solicitud de despacho conserva seleccion multiple de cargas `STORED` de una sola empresa. La
pantalla muestra persistentemente cantidad de cargas, piezas totales y peso total antes de confirmar.
El selector de metodo ofrece unicamente `SEA`, `AIR` y `LAND`.

El detalle separa:

- Estado y acciones del despacho.
- Cargas incluidas.
- Documentos globales del despacho.
- Historial del despacho.

Subir un BL ocurre exclusivamente en `Documentos del despacho`. No aparece una carga arbitraria como
destino ni se replica el documento dentro de su expediente.

La accion `Despachar` registra la salida fisica. La accion `Completar` aparece despues y solo se
habilita cuando todas las cargas salieron, los requisitos aplicables estan verificados y existe un BL
global `READY`. `Completar` cierra la solicitud, pero no marca las cargas como entregadas.

## Componentes

| Componente | Responsabilidad |
| --- | --- |
| `BarraBusquedaCargas` | Omnibox, debounce y envio de `q` |
| `PanelFiltrosCargas` | Filtros combinados y sincronizacion con URL |
| `ChipsFiltrosActivos` | Estado visible de filtros y limpieza individual |
| `TablaCargas` | Vista operativa de escritorio |
| `ListaCargasMovil` | Resumen y menu de acciones tactil |
| `SelectorEstadoEnLinea` | Transiciones disponibles, motivo y conflicto |
| `FormularioCarga` | Campos compartidos entre crear y editar |
| `EditorPeso` | Conversion bidireccional sin ciclos |
| `EditorPiezas` | Invariancia de una pieza y total calculado |
| `PanelDocumentosCarga` | Requisitos, catalogo permitido y uploads |
| `PanelDocumentosDespacho` | Adjuntos exclusivos del despacho |
| `ProgresoExportacion` | Polling acotado y descarga del ZIP terminado |

Las consultas siguen agrupadas por dominio en `frontend/src/features/`. Los tipos se regeneran desde
OpenAPI; no se mantienen interfaces manuales duplicadas para los contratos nuevos.

## Estados de interfaz

Cada operacion contempla explicitamente:

- Cargando con dimensiones reservadas para evitar saltos.
- Lista vacia diferenciada de resultado sin coincidencias.
- Error recuperable con reintento.
- Sin permiso tratado como recurso no disponible.
- Upload en progreso, procesamiento, listo y fallido.
- Mutacion pendiente con controles deshabilitados localmente.
- Conflicto de version con recarga, nunca sobrescritura silenciosa.

## Accesibilidad y responsive

- Todos los campos tienen label persistente.
- Los botones de solo icono tienen `aria-label` y tooltip.
- El selector de estado y los modales soportan teclado y devuelven el foco al activador.
- Los errores se anuncian mediante una region `aria-live` sin mover el formulario.
- Ningun control depende solo del color para comunicar estado.
- La tabla pasa a lista antes de que sus columnas se compriman o superpongan.
- Los botones mantienen area tactil minima de 40 por 40 px.

## Criterios de aceptacion UX

- Crear una carga valida y llegar al panel de archivos requiere una sola confirmacion.
- No es posible quitar la ultima pieza ni enviar una carga sin piezas.
- Cambiar cualquiera de los pesos actualiza el otro sin perdida de foco.
- Una carga puede encontrarse por SHP, WR, shipper, carrier o referencia desde la misma pantalla.
- Se pueden combinar empresa, estado, carrier y rango de fechas, y la URL conserva la consulta.
- Operaciones cambia un estado desde la tabla sin abrir el detalle y el historial refleja el evento.
- Un cliente nunca observa tipos documentales internos ni acciones administrativas.
- `CLIENT_USER` y `CLIENT_ADMIN` observan las mismas acciones empresariales.
- El formulario de despacho no ofrece `PICKUP`.
- Un BL cargado desde despacho aparece solo en ese despacho.
- Una exportacion grande permite abandonar y regresar a la pagina sin perder el trabajo.
- Playwright valida escritorio y movil sin desbordes, superposiciones ni cambios de layout durante
  cargas y mutaciones.

## Secuencia de construccion

1. Generar contratos OpenAPI despues de cerrar los schemas backend.
2. Extraer `FormularioCarga`, `EditorPeso` y `EditorPiezas`.
3. Reconstruir listado, busqueda combinada y estado en linea.
4. Reorganizar detalle en Resumen, Documentos e Historial.
5. Separar paneles de documentos de carga y despacho.
6. Incorporar progreso de procesamiento y exportaciones.
7. Completar pruebas de componentes, integracion y Playwright responsive.
