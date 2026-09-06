"use client";

import { useQuery } from "@tanstack/react-query";
import { api, exigirDatos } from "@/lib/api/client";
import type { CapacidadesAsistente } from "@/lib/api/tipos";

export function useCapacidadesAsistente() {
  return useQuery({
    queryKey: ["copilot", "capabilities"],
    queryFn: async (): Promise<CapacidadesAsistente> =>
      exigirDatos(await api.GET("/api/v1/copilot/capabilities", {})),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
}
