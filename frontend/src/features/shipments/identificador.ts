/** Referencia comercial visible de una carga: WR en Miami, factura en tránsito. */
export function identificadorCarga(carga: { wr?: string | null; invoice?: string | null }) {
  return carga.wr || carga.invoice || "Sin factura o WR";
}
