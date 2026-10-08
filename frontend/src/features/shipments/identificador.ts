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

/**
 * El identificador más la factura, para las vistas compactas (móvil, resumen,
 * solicitud de despacho): el cliente reconoce su carga por la factura, y en
 * Miami el WR solo no le alcanzaba (pedido de AMVARMAR, 2026-10-08).
 */
export function identificadorConFactura(carga: {
  wr?: string | null;
  bl?: string | null;
  invoice?: string | null;
}) {
  const base = identificadorCarga(carga);
  return carga.invoice && (carga.wr || carga.bl) ? `${base} · Factura ${carga.invoice}` : base;
}
