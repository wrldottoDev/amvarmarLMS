"use client";

import PaginaCargas from "@/app/(client)/shipments/page";

/**
 * Historial de despachos (ADR-0007).
 *
 * Mismo listado, filtrado a solo cargas archivadas. Cumplieron su retención
 * operativa: ya no admiten cambios de estado, documentos nuevos ni ediciones —
 * el backend lo rechaza igual si algo llegara a intentarlo.
 */
export default function PaginaHistorial() {
  return <PaginaCargas archivadas />;
}
