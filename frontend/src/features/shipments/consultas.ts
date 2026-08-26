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
