/**
 * Preguntas sugeridas según la pantalla (pedido de AMVARMAR, 2026-10-08):
 * el chat vacío ya propone lo que la persona suele necesitar ahí, en vez de
 * una caja en blanco.
 */
const PARA_CLIENTE: [prefijo: string, preguntas: string[]][] = [
  ["/miami", ["¿Qué cargas puedo despachar?", "¿Qué me falta para despachar?", "¿Cómo pido un despacho?"]],
  ["/transito", ["¿Cuándo llega mi carga?", "¿Qué significa transbordo?", "¿Qué es FCL y LCL?"]],
  ["/despachos", ["¿En qué va mi despacho?", "¿Dónde descargo el BL?", "¿Puedo cancelar una solicitud?"]],
  ["/shipments/", ["¿Qué le falta a esta carga?", "¿Qué documentos debería subir?", "¿Dónde está esta carga?"]],
  ["/shipments/historial", ["¿Dónde están mis cargas entregadas?", "¿Cómo descargo los documentos?"]],
];

const PARA_OPERACIONES: [prefijo: string, preguntas: string[]][] = [
  ["/miami", ["¿Qué cargas están por llegar a Miami?", "Pasá a almacenada la carga con WR…", "¿Qué despachos están pendientes?"]],
  ["/transito", ["¿Qué reportes de tránsito llegan esta semana?", "Pasá a transbordo el BL…"]],
  ["/cargas/nueva", ["Leé esta factura y armá la prealerta", "¿Qué datos necesito para registrar una carga marítima?"]],
  ["/despachos", ["¿Qué solicitudes de despacho están pendientes?", "¿Cómo apruebo un despacho?"]],
];

const GENERALES_CLIENTE = ["¿Qué cargas tengo en Miami?", "¿Qué tengo pendiente?", "¿Cómo pido un despacho?"];
const GENERALES_OPERACIONES = ["¿Qué tengo pendiente hoy?", "¿Qué cargas llegan esta semana?", "¿Cómo registro una carga?"];

export function sugerenciasPara(ruta: string, esCliente: boolean): string[] {
  const tabla = esCliente ? PARA_CLIENTE : PARA_OPERACIONES;
  // El prefijo más largo que coincide: "/shipments/historial" antes que "/shipments/".
  const coincidencia = tabla
    .filter(([prefijo]) => ruta.startsWith(prefijo))
    .sort((a, b) => b[0].length - a[0].length)[0];
  return coincidencia?.[1] ?? (esCliente ? GENERALES_CLIENTE : GENERALES_OPERACIONES);
}
