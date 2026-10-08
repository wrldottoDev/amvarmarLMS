"use client";

import { useQuery } from "@tanstack/react-query";
import { api, exigirDatos } from "@/lib/api/client";

export type CampoSugerible = "shipper" | "carrier" | "tariff_code" | "description";

/**
 * Autocompletado nativo (`<datalist>`) con lo que esa empresa ya usó en el
 * campo, lo más frecuente primero (pedido de AMVARMAR, 2026-10-08). El input
 * lo enlaza con `list={id}`. Sin empresa elegida no pide nada.
 */
export function ListaSugerencias({
  id,
  campo,
  empresa,
}: {
  id: string;
  campo: CampoSugerible;
  empresa: string | null | undefined;
}) {
  const consulta = useQuery({
    queryKey: ["sugerencias", empresa, campo],
    queryFn: async () =>
      exigirDatos(
        await api.GET("/api/v1/shipments/sugerencias", {
          params: { query: { campo, company_id: empresa as string } },
        }),
      ),
    enabled: Boolean(empresa),
    staleTime: 5 * 60 * 1000,
  });
  return (
    <datalist id={id}>
      {(consulta.data ?? []).map((valor) => (
        <option key={valor} value={valor} />
      ))}
    </datalist>
  );
}
