"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { ClockArrowUp, Download, History } from "lucide-react";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { CargandoPagina, EstadoVacio } from "@/components/ui/estados-pagina";
import { api, exigirDatos } from "@/lib/api/client";
import { formatearFechaHora } from "@/lib/utilidades";

function esRegistroAtrasado(ocurrio: string, registrado: string) {
  const diferencia = Math.abs(new Date(registrado).getTime() - new Date(ocurrio).getTime());
  return diferencia > 5 * 60 * 1000;
}

export function TimelineCarga({ cargaId }: { cargaId: string }) {
  const consulta = useInfiniteQuery({
    queryKey: ["carga", cargaId, "timeline"],
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) =>
      exigirDatos(
        await api.GET("/api/v1/shipments/{shipment_id}/timeline", {
          params: { path: { shipment_id: cargaId }, query: { limit: 20, cursor: pageParam } },
        }),
      ),
    getNextPageParam: (pagina) => (pagina.has_more && pagina.next_cursor ? pagina.next_cursor : undefined),
  });

  const eventos = consulta.data?.pages.flatMap((pagina) => pagina.items) ?? [];

  if (consulta.isLoading) return <CargandoPagina texto="Cargando línea de tiempo" />;
  if (consulta.error) return <AvisoError error={consulta.error} />;
  if (!eventos.length) {
    return <EstadoVacio titulo="Sin eventos registrados" descripcion="La actividad de esta carga aparecerá aquí." />;
  }

  return (
    <div>
      <ol className="relative ml-2 border-l-2 border-[#dce2e5]">
        {eventos.map((evento) => {
          const atrasado = esRegistroAtrasado(evento.occurred_at, evento.recorded_at);
          return (
            <li key={evento.id} className="relative pb-8 pl-7 last:pb-2">
              <span className="absolute -left-[9px] top-0 grid size-4 place-items-center rounded-full border-2 border-white bg-[var(--mar)] shadow-sm" />
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <h3 className="text-sm font-semibold">{evento.title}</h3>
                  {evento.description ? <p className="mt-1 text-sm leading-6 text-[var(--texto-secundario)]">{evento.description}</p> : null}
                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--texto-secundario)]">
                    {evento.actor ? <span>{evento.actor}</span> : null}
                    {evento.location ? <span>{evento.location}</span> : null}
                  </div>
                </div>
                <div className="shrink-0 text-left sm:text-right">
                  <time className="text-xs font-medium">{formatearFechaHora(evento.occurred_at)}</time>
                  {atrasado ? (
                    <p className="mt-1 inline-flex items-center gap-1 text-[11px] font-semibold text-[var(--advertencia)] sm:flex">
                      <ClockArrowUp className="size-3" aria-hidden="true" /> Registro atrasado
                    </p>
                  ) : null}
                </div>
              </div>
            </li>
          );
        })}
      </ol>

      {consulta.hasNextPage ? (
        <div className="mt-5 flex justify-center">
          <Boton variante="secundario" cargando={consulta.isFetchingNextPage} onClick={() => void consulta.fetchNextPage()}>
            <Download className="size-4" aria-hidden="true" />
            Cargar eventos anteriores
          </Boton>
        </div>
      ) : (
        <div className="mt-5 flex items-center justify-center gap-2 text-xs text-[var(--texto-secundario)]">
          <History className="size-3.5" aria-hidden="true" /> Inicio del historial
        </div>
      )}
    </div>
  );
}
