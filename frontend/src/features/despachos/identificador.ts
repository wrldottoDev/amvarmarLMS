/** Referencias comerciales que deben protagonizar la solicitud de despacho. */
export function identificadorDespacho(identificadores: string[]) {
  if (identificadores.length === 0) return "Sin WR, BL o factura";
  return identificadores.join(" · ");
}
