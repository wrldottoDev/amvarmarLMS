/** Referencia comercial visible: WR para Miami y BL para reportes de tránsito. */
export function identificadorCarga(carga: {
  wr?: string | null;
  bl?: string | null;
  invoice?: string | null;
}) {
  if (carga.wr) return `WR ${carga.wr}`;
  if (carga.bl) return `BL ${carga.bl}`;
  if (carga.invoice) return `Factura ${carga.invoice}`;
  return "Sin WR, BL o factura";
}
