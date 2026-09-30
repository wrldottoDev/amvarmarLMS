"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, exigirDatos } from "@/lib/api/client";
import type { CargaResumen } from "@/lib/api/tipos";
import { formatearFecha } from "@/lib/utilidades";
import { identificadorCarga } from "./identificador";

const clave = ["preferencias", "columnas"] as const;

const ocultasCliente = new Set(["tracking", "carrier", "po", "container"]);

export function columnaPermitida(
  columna: string,
  esCliente: boolean,
  tipoOrigen?: "MIAMI" | "TRANSIT",
) {
  if (esCliente && ocultasCliente.has(columna)) return false;
  if (esCliente && tipoOrigen === "TRANSIT" && columna === "invoice") return false;
  if (tipoOrigen === "TRANSIT" && columna === "fecha") return false;
  if (tipoOrigen !== "TRANSIT" && columna === "eta") return false;
  return true;
}

export function columnasParaListado(
  visibles: string[],
  esCliente: boolean,
  tipoOrigen?: "MIAMI" | "TRANSIT",
) {
  const resultado = visibles.filter((columna) =>
    columnaPermitida(columna, esCliente, tipoOrigen),
  );
  if (tipoOrigen !== "TRANSIT") return resultado;

  if (esCliente && !resultado.includes("bl")) resultado.splice(1, 0, "bl");
  if (!resultado.includes("shipper")) resultado.push("shipper");
  if (!resultado.includes("eta")) resultado.push("eta");
  return resultado;
}

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
  identificador: identificadorCarga,
  empresa: () => "",
  invoice: (c) => c.invoice || "—",
  bl: (c) => c.bl || "—",
  amvar: (c) => c.amvar || "—",
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
  eta: (c) => formatearFecha(c.estimated_arrival_at),
};

/** Columnas que se alinean a la derecha por ser números. */
export const columnasNumericas = new Set(["foots_cft", "peso", "bultos"]);
