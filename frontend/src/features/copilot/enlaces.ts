/**
 * Detecta códigos de carga (SHP-2026-000123) y de despacho (DSP-2026-000123)
 * en el texto que devuelve el asistente, para volverlos enlaces internos.
 *
 * Los ejecutores de lectura (`copilot/executors.py`) nunca le pasan el UUID
 * interno al modelo — solo el código legible — así que el enlace es al
 * listado filtrado por ese código, no a la ruta de detalle por id. Las
 * cargas soportan ese filtro (`/shipments?q=`); los despachos todavía no
 * tienen uno equivalente, así que van al listado general.
 */

export interface FragmentoTexto {
  texto: string;
  href?: string;
}

const PATRON_CARGA = /\bSHP-\d{4}-\d{6}\b/g;
const PATRON_DESPACHO = /\bDSP-\d{4}-\d{6}\b/g;

export function fragmentarConEnlaces(texto: string): FragmentoTexto[] {
  const coincidencias: { indice: number; largo: number; href: string }[] = [];

  for (const coincidencia of texto.matchAll(PATRON_CARGA)) {
    coincidencias.push({
      indice: coincidencia.index,
      largo: coincidencia[0].length,
      href: `/shipments?q=${encodeURIComponent(coincidencia[0])}`,
    });
  }
  for (const coincidencia of texto.matchAll(PATRON_DESPACHO)) {
    coincidencias.push({ indice: coincidencia.index, largo: coincidencia[0].length, href: "/despachos" });
  }
  coincidencias.sort((a, b) => a.indice - b.indice);

  const fragmentos: FragmentoTexto[] = [];
  let cursor = 0;
  for (const { indice, largo, href } of coincidencias) {
    if (indice < cursor) continue;
    if (indice > cursor) fragmentos.push({ texto: texto.slice(cursor, indice) });
    fragmentos.push({ texto: texto.slice(indice, indice + largo), href });
    cursor = indice + largo;
  }
  if (cursor < texto.length) fragmentos.push({ texto: texto.slice(cursor) });
  return fragmentos;
}
