# Cómo subo un documento que falta

<!-- palabras_clave: subir documento, documento pendiente, falta un documento, adjuntar archivo, requisito documental -->

En el detalle de la carga hay un panel de documentos con la lista de requisitos: cada uno indica qué
documento hace falta y trae su propio botón para subirlo. Si necesitás adjuntar algo que no está en la
lista de requisitos, usá **Adjuntar otro documento**.

- Subir un archivo válido mueve el requisito de `PENDING` a `UPLOADED` — todavía no queda `VERIFIED`.
  La verificación la hace Operaciones después de revisar el archivo.
- Un requisito puede terminar `REJECTED` (con motivo) y volver a `UPLOADED` si lo corregís, o quedar
  `NOT_APPLICABLE` / `WAIVED` cuando la política lo permite.
- Los documentos habituales de una carga son factura comercial y packing list (en todas las cargas), SLI
  (solo si la carga sale de la bodega de Miami) y, si corresponde, un permiso especial. El BL pertenece
  al despacho, no a una carga individual.
- Un requisito marcado como bloqueante tiene que resolverse antes del paso que protege (por ejemplo,
  antes del despacho).
