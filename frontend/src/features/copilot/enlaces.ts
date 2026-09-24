/**
 * Convierte en enlaces internos lo que el asistente escribe: los códigos de
 * carga (SHP-2026-000123), los de despacho (DSP-2026-000123) y las rutas de
 * la aplicación entre backticks (`/despachos/nuevo`).
 *
 * Los ejecutores de lectura (`copilot/executors.py`) nunca le pasan el UUID
 * interno al modelo — solo el código legible — así que el enlace es al
 * listado filtrado por ese código, no a la ruta de detalle por id. Las
 * cargas soportan ese filtro (`/shipments?q=`); los despachos todavía no
 * tienen uno equivalente, así que van al listado general.
 *
 * Las rutas se validan contra una lista blanca en vez de enlazar cualquier
 * cosa que empiece con `/`: el system prompt le pide al modelo que escriba
 * rutas reales, pero un modelo puede equivocarse, y un enlace a una pantalla
 * inexistente es peor que texto plano — promete algo y lleva a un 404.
 */

/** Rutas a las que el asistente puede mandar. Si no está acá, queda como
 * texto: preferimos una instrucción sin enlace antes que un enlace roto. */
const RUTAS_CONOCIDAS = new Set([
  "/operaciones",
  "/dashboard",
  "/shipments",
  "/shipments/historial",
  "/despachos",
  "/despachos/nuevo",
  "/avisos",
  "/inventario",
  "/empresas",
  "/usuarios",
  "/sesiones",
  "/cuenta",
  "/cargas/nueva",
]);

export interface FragmentoTexto {
  texto: string;
  href?: string;
}

const PATRON_CARGA = /\bSHP-\d{4}-\d{6}\b/g;
const PATRON_DESPACHO = /\bDSP-\d{4}-\d{6}\b/g;
// Con backticks: es como el prompt le pide al modelo que las escriba, y evita
// enlazar una barra suelta en medio de una frase.
const PATRON_RUTA = /`(\/[a-z0-9/-]*)`/g;

export function fragmentarConEnlaces(texto: string): FragmentoTexto[] {
  const coincidencias: { indice: number; largo: number; href: string; texto?: string }[] = [];

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
  for (const coincidencia of texto.matchAll(PATRON_RUTA)) {
    const ruta = coincidencia[1];
    if (!RUTAS_CONOCIDAS.has(ruta)) continue;
    coincidencias.push({
      indice: coincidencia.index,
      largo: coincidencia[0].length,
      href: ruta,
      // Sin los backticks: son sintaxis para el modelo, no para quien lee.
      texto: ruta,
    });
  }
  coincidencias.sort((a, b) => a.indice - b.indice);

  const fragmentos: FragmentoTexto[] = [];
  let cursor = 0;
  for (const { indice, largo, href, texto: etiqueta } of coincidencias) {
    if (indice < cursor) continue;
    if (indice > cursor) fragmentos.push({ texto: texto.slice(cursor, indice) });
    fragmentos.push({ texto: etiqueta ?? texto.slice(indice, indice + largo), href });
    cursor = indice + largo;
  }
  if (cursor < texto.length) fragmentos.push({ texto: texto.slice(cursor) });
  return fragmentos;
}
