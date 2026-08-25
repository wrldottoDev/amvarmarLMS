"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { Boxes, Download, PackagePlus } from "lucide-react";
import { useMemo, useState } from "react";
import { FiltrosCargas, filtrosIniciales, type FiltrosCarga } from "@/components/shipments/filtros-cargas";
import { ListadoCargas } from "@/components/shipments/listado-cargas";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { CargandoPagina, EstadoVacio } from "@/components/ui/estados-pagina";
import Link from "next/link";
import { useSesion } from "@/features/auth/contexto-sesion";
import { api, exigirDatos } from "@/lib/api/client";

function inicioDia(valor: string) {
  return valor ? new Date(`${valor}T00:00:00`).toISOString() : undefined;
}

function finalDia(valor: string) {
  return valor ? new Date(`${valor}T23:59:59.999`).toISOString() : undefined;
}

export default function PaginaCargas() {
  const [filtros, setFiltros] = useState<FiltrosCarga>(filtrosIniciales);
  const { usuario } = useSesion();

  const consulta = useInfiniteQuery({
    queryKey: ["cargas", filtros],
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) =>
      exigirDatos(
        await api.GET("/api/v1/shipments", {
          params: {
            query: {
              limit: 25,
              cursor: pageParam,
              status: filtros.estados.length ? filtros.estados : undefined,
              eta_from: inicioDia(filtros.etaDesde),
              eta_to: finalDia(filtros.etaHasta),
              q: filtros.q || undefined,
              archived: false,
            },
          },
        }),
      ),
    getNextPageParam: (ultimaPagina) =>
      ultimaPagina.has_more && ultimaPagina.next_cursor ? ultimaPagina.next_cursor : undefined,
  });

  const cargas = useMemo(() => consulta.data?.pages.flatMap((pagina) => pagina.items) ?? [], [consulta.data]);

  return (
    <div className="space-y-6">
      <header className="flex items-end justify-between gap-4">
        <div>
          <p className="text-xs font-bold uppercase text-[var(--marca)]">Seguimiento</p>
          <h1 className="mt-1 text-2xl font-bold">Cargas</h1>
          <p className="mt-1 text-sm text-[var(--texto-secundario)]">{cargas.length} cargadas en esta vista</p>
        </div>
        {usuario?.empresa ? (
          <span className="hidden size-11 place-items-center rounded-md bg-[#e8f0f2] text-[var(--mar)] sm:grid">
            <Boxes className="size-5" aria-hidden="true" />
          </span>
        ) : (
          // Operaciones alimenta el sistema: el alta va donde ya está mirando
          // las cargas, no escondida en otro menú.
          <Link
            href="/cargas/nueva"
            className="flex h-10 shrink-0 items-center gap-2 rounded-md bg-[var(--mar)] px-4 text-sm font-semibold text-white hover:opacity-90"
          >
            <PackagePlus className="size-4" aria-hidden="true" />
            Nueva carga
          </Link>
        )}
      </header>

      <FiltrosCargas aplicar={setFiltros} />

      {consulta.isLoading ? <CargandoPagina texto="Cargando cargas" /> : null}
      {consulta.error ? <AvisoError error={consulta.error} /> : null}

      {!consulta.isLoading && !consulta.error && cargas.length === 0 ? (
        <div className="rounded-lg border bg-white">
          <EstadoVacio titulo="No hay cargas" descripcion="No se encontraron resultados con los filtros aplicados." />
        </div>
      ) : null}

      {cargas.length ? <ListadoCargas cargas={cargas} esCliente={Boolean(usuario?.empresa)} /> : null}

      {consulta.hasNextPage ? (
        <div className="flex justify-center pt-2">
          <Boton variante="secundario" cargando={consulta.isFetchingNextPage} onClick={() => void consulta.fetchNextPage()}>
            <Download className="size-4" aria-hidden="true" />
            Cargar más
          </Boton>
        </div>
      ) : null}
    </div>
  );
}
