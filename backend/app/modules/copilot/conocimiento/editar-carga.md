# Cómo edito una carga ya creada

<!-- palabras_clave: editar carga, corregir carga, modificar carga, cambiar datos de la carga, arreglar un dato -->

Solo Operaciones edita una carga, y solo mientras no salió de bodega (`PRE_ALERT`, `IN_TRANSIT`,
`RECEIVED` o `STORED`). Un cliente nunca ve el botón **Editar** en el detalle de su carga — no es un
permiso que se pueda pedir, es una regla del sistema.

Desde el detalle de la carga, el botón **Editar** abre un formulario donde se corrige:

- Identificadores: Warehouse Receipt, factura, tracking, orden de compra, contenedor.
- Datos comerciales: método de transporte, shipper, carrier.
- Peso y volumen: peso (kg o lb), peso volumétrico, volumen en m³, pies cúbicos.
- El desglose de piezas completo.
- Destino (ubicación y dirección de entrega) y la descripción de qué viene.

Lo que **no** se corrige desde ahí: el estado (eso es una transición aparte, con su propio registro) ni
la empresa dueña de la carga — mover una carga de cliente es un movimiento contable, no una corrección de
tipeo. Solo se mandan los campos que se tocaron, así que dos personas editando cosas distintas no se
pisan.
