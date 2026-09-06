"use client";

import { useQuery } from "@tanstack/react-query";
import { api, exigirDatos } from "@/lib/api/client";

/**
 * Catálogo de tipos de documento.
 *
 * El backend ya filtra por contexto, rol y scope. La interfaz consume ese
 * catálogo tal cual para no ofrecer tipos internos a clientes.
 */
export function useTiposDeDocumento(contexto: "SHIPMENT" | "DISPATCH") {
  return useQuery({
    queryKey: ["catalogos", "tipos-documento", contexto],
    queryFn: async () =>
      exigirDatos(
        await api.GET("/api/v1/document-types", {
          params: { query: { context: contexto } },
        }),
      ),
    staleTime: 30 * 60 * 1000,
  });
}
