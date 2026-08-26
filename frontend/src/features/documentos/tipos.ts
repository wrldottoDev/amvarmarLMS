"use client";

import { useQuery } from "@tanstack/react-query";
import { api, exigirDatos } from "@/lib/api/client";

/**
 * Catálogo de tipos de documento.
 *
 * Se lee del expediente de una carga cualquiera porque no hay endpoint propio:
 * los tipos son los mismos para todo el sistema. Cachea largo — cambian cuando
 * cambia el negocio, no durante una sesión.
 */
export function useTiposDeDocumento() {
  return useQuery({
    queryKey: ["catalogos", "tipos-documento"],
    queryFn: async () => {
      const cargas = exigirDatos(
        await api.GET("/api/v1/shipments", { params: { query: { limit: 1 } } }),
      );
      const primera = cargas.items[0];
      if (!primera) return [];

      const expediente = exigirDatos(
        await api.GET("/api/v1/shipments/{shipment_id}/documents", {
          params: { path: { shipment_id: primera.id } },
        }),
      );
      return expediente.tipos;
    },
    staleTime: 30 * 60 * 1000,
  });
}
