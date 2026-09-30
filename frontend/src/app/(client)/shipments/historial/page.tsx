"use client";

import PaginaCargas from "@/app/(client)/shipments/page";

/**
 * Historial de despachos (ADR-0007).
 *
 * Mismo listado, filtrado a cargas despachadas o entregadas de cualquier origen.
 */
export default function PaginaHistorial() {
  return <PaginaCargas archivadas />;
}
