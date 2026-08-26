"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, exigirDatos } from "@/lib/api/client";
import type { CargaResumen } from "@/lib/api/tipos";
import { formatearFecha } from "@/lib/utilidades";

const clave = ["preferencias", "columnas"] as const;

export function usePreferenciaColumnas() {
  return useQuery({
    queryKey: clave,
    queryFn: async () =>
      exigirDatos(await api.GET("/api/v1/shipments/preferencias/columnas", {})),
    // Es preferencia de quien mira, no dato de negocio: no hace falta
    // refrescarla al volver a la pestaña.
    staleTime: 10 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
}

export function useGuardarColumnas() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (visibles: string[]) =>
      exigirDatos(
        await api.PUT("/api/v1/shipments/preferencias/columnas", { body: { visibles } }),
      ),
    onSuccess: (datos) => cliente.setQueryData(clave, datos),
  });
}

/**
 * Cómo se pinta cada columna.
 *
 * Vive acá y no dentro de la tabla para que agregar una columna sea tocar un
 * solo lugar: el backend la ofrece, esto dice cómo se ve, y la tabla no
 * necesita saber cuáles existen.
 */
export const contenidoColumna: Record<string, (carga: CargaResumen) => string> = {
  identificador: (c) => c.wr || c.invoice || c.shipment_number,
  empresa: () => "",
  estado: (c) => c.status,
  shipper: (c) => c.shipper || "—",
  carrier: (c) => c.carrier || "—",
  foots_cft: (c) => (c.foots_cft != null ? `${c.foots_cft}` : "—"),
  tracking: (c) => c.tracking || "—",
  po: (c) => c.po || "—",
  container: (c) => c.container || "—",
  peso: (c) => {
    const kg = c.weight_kg != null ? `${c.weight_kg} kg` : null;
    const lb = c.weight_lb != null ? `${c.weight_lb} lb` : null;
    return [kg, lb].filter(Boolean).join(" · ") || "—";
  },
  bultos: (c) => `${c.package_count}`,
  pendientes: () => "",
  fecha: (c) => formatearFecha(c.created_at),
};

/** Columnas que se alinean a la derecha por ser números. */
export const columnasNumericas = new Set(["foots_cft", "peso", "bultos"]);
