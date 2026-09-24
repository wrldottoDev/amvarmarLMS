"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, exigirDatos } from "@/lib/api/client";
import type { paths } from "@/lib/api/generated";

type CuerpoActualizar =
  paths["/api/v1/shipments/{shipment_id}"]["patch"]["requestBody"]["content"]["application/json"];

export function useCarga(cargaId: string) {
  return useQuery({
    queryKey: ["carga", cargaId],
    queryFn: async () =>
      exigirDatos(
        await api.GET("/api/v1/shipments/{shipment_id}", {
          params: { path: { shipment_id: cargaId } },
        }),
      ),
  });
}

type CuerpoPiezas =
  paths["/api/v1/shipments/{shipment_id}/packages"]["put"]["requestBody"]["content"]["application/json"];

/**
 * Reemplaza el desglose completo de piezas.
 *
 * Reemplazo y no parcheo fila por fila: el formulario muestra la lista entera y
 * quien la guarda cree estar guardando eso. La lista vacía la rechaza el
 * backend, y la base tiene el mismo invariante con un constraint diferible.
 */
export function useReemplazarPiezas(cargaId: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (cuerpo: CuerpoPiezas) =>
      exigirDatos(
        await api.PUT("/api/v1/shipments/{shipment_id}/packages", {
          params: { path: { shipment_id: cargaId } },
          body: cuerpo,
        }),
      ),
    onSuccess: () => {
      cliente.invalidateQueries({ queryKey: ["carga", cargaId] });
      cliente.invalidateQueries({ queryKey: ["cargas"] });
      cliente.invalidateQueries({ queryKey: ["timeline", cargaId] });
    },
  });
}

/**
 * Corrección de datos de una carga.
 *
 * `row_version` viaja en el cuerpo y lo pone quien llama con el valor que leyó:
 * si otra persona guardó en el medio, el backend responde 409 en vez de dejar
 * que el segundo pise al primero en silencio.
 */
export function useActualizarCarga(cargaId: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (cambios: CuerpoActualizar) =>
      exigirDatos(
        await api.PATCH("/api/v1/shipments/{shipment_id}", {
          params: { path: { shipment_id: cargaId } },
          body: cambios,
        }),
      ),
    onSuccess: () => {
      cliente.invalidateQueries({ queryKey: ["carga", cargaId] });
      cliente.invalidateQueries({ queryKey: ["cargas"] });
      cliente.invalidateQueries({ queryKey: ["timeline", cargaId] });
    },
  });
}
