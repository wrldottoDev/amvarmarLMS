/**
 * Si Operaciones puede revisar (verificar o rechazar) un requisito documental.
 *
 * El backend exige las mismas tres cosas: permiso `documents.verify`, requisito
 * en `UPLOADED` y un documento `READY`. Mientras el archivo se procesa se
 * muestra la revisión deshabilitada, para que se entienda por qué todavía no
 * se puede aprobar.
 */
export function revisionDeRequisito(
  requisito: { status: string; document_id?: string | null },
  estadoDocumento: string | undefined,
  puedeRevisar: boolean,
): { mostrar: boolean; listo: boolean; aviso?: string } {
  if (!puedeRevisar || requisito.status !== "UPLOADED" || !requisito.document_id) {
    return { mostrar: false, listo: false };
  }
  if (estadoDocumento !== "READY") {
    return { mostrar: true, listo: false, aviso: "El archivo todavía se está procesando." };
  }
  return { mostrar: true, listo: true };
}
