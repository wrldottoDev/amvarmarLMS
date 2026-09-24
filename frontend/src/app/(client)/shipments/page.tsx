"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { Boxes, Download, PackagePlus } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useMemo, useState } from "react";
import {
  FiltrosCargas,
  filtrosDesdeParametros,
  parametrosDeFiltros,
  type FiltrosCarga,
} from "@/components/shipments/filtros-cargas";
import { ListadoCargas } from "@/components/shipments/listado-cargas";
import { SelectorColumnas } from "@/components/shipments/selector-columnas";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { CargandoPagina, EstadoVacio } from "@/components/ui/estados-pagina";
import { useSesion } from "@/features/auth/contexto-sesion";
import { api, exigirDatos } from "@/lib/api/client";

function inicioDia(valor: string) {
  return valor ? new Date(`${valor}T00:00:00`).toISOString() : undefined;
}

function finalDia(valor: string) {
  return valor ? new Date(`${valor}T23:59:59.999`).toISOString() : undefined;
}

export default function PaginaCargas({
  inventario = false,
  archivadas = false,
}: {
  inventario?: boolean;
  /** Historial de despachos (ADR-0007): solo cargas ya archivadas, de solo
   * lectura — sin alta, sin cambio de estado. */
  archivadas?: boolean;
}) {
  return (
    <Suspense fallback={<CargandoPagina texto="Preparando cargas" />}>
      <ContenidoCargas inventario={inventario} archivadas={archivadas} />
    </Suspense>
  );
}

function ContenidoCargas({
  inventario,
  archivadas,
}: {
  inventario: boolean;
  archivadas: boolean;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const cadenaParametros = searchParams.toString();
  const filtros = useMemo(
    () => filtrosDesdeParametros(new URLSearchParams(cadenaParametros)),
    [cadenaParametros],
  );
  const [verOcultas, setVerOcultas] = useState(false);
  const { usuario } = useSesion();
  const esCliente = Boolean(usuario?.empresa);
  const puedeCrear = usuario?.permisos.includes("shipments.create");

  const aplicarFiltros = useCallback(
    (nuevos: FiltrosCarga) => {
      const parametros = parametrosDeFiltros(nuevos).toString();
      router.replace(parametros ? `${pathname}?${parametros}` : pathname, { scroll: false });
    },
    [pathname, router],
  );

  const consulta = useInfiniteQuery({
    queryKey: ["cargas", filtros, inventario, archivadas, verOcultas],
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) =>
      exigirDatos(
        await api.GET("/api/v1/shipments", {
          params: {
            query: {
              limit: 25,
              cursor: pageParam,
              // La vista Inventario ignora el filtro de estado a propósito:
              // es "todo lo que existe", que es como se usaba en el sistema
              // viejo para buscar algo sin saber en qué punto estaba.
              status: !inventario && !archivadas && filtros.estados.length ? filtros.estados : undefined,
              incluir_ocultas: verOcultas || undefined,
              eta_from: inicioDia(filtros.etaDesde),
              eta_to: finalDia(filtros.etaHasta),
              q: filtros.q || undefined,
              shipment_number: filtros.shipmentNumber || undefined,
              wr: filtros.wr || undefined,
              shipper: filtros.shipper || undefined,
              carrier: filtros.carrier || undefined,
              reference: filtros.reference || undefined,
              reference_type: filtros.referenceType || undefined,
              company_id: filtros.companyId || undefined,
              only_archived: archivadas || undefined,
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
          <p className="text-xs font-bold uppercase text-[var(--marca)]">
            {archivadas ? "Historial" : inventario ? "Inventario" : "Seguimiento"}
          </p>
          <h1 className="mt-1 text-2xl font-bold">
            {archivadas ? "Historial de despachos" : inventario ? "Inventario" : "Cargas"}
          </h1>
          <p className="mt-1 text-sm text-[var(--texto-secundario)]">
            {archivadas
              ? "Cargas archivadas, de solo lectura. Cumplieron su retención y ya no admiten cambios."
              : inventario
                ? "Todas las cargas, sin filtrar por estado."
                : `${cargas.length} cargadas en esta vista`}
          </p>
        </div>
        {archivadas ? null : !puedeCrear ? (
          <span className="hidden size-11 place-items-center rounded-md bg-[var(--marca-tenue)] text-[var(--mar)] sm:grid">
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

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex-1">
          <FiltrosCargas
            key={cadenaParametros}
            valor={filtros}
            aplicar={aplicarFiltros}
            mostrarEmpresa={!esCliente}
          />
        </div>
        <div className="flex items-center gap-2">
          {esCliente ? null : (
            <label
              className="flex h-10 cursor-pointer items-center gap-2 rounded-md border px-3 text-sm"
              title="Las cargas ocultas no se borraron: siguen con sus documentos y su historial"
            >
              <input
                type="checkbox"
                className="size-4 accent-[var(--mar)]"
                checked={verOcultas}
                onChange={(evento) => setVerOcultas(evento.target.checked)}
              />
              Ver ocultas
            </label>
          )}
          <SelectorColumnas />
        </div>
      </div>

      {consulta.isLoading ? <CargandoPagina texto="Cargando cargas" /> : null}
      {consulta.error ? <AvisoError error={consulta.error} /> : null}

      {!consulta.isLoading && !consulta.error && cargas.length === 0 ? (
        <div className="rounded-lg border bg-[var(--superficie)]">
          <EstadoVacio titulo="No hay cargas" descripcion="No se encontraron resultados con los filtros aplicados." />
        </div>
      ) : null}

      {cargas.length ? (
        <ListadoCargas
          cargas={cargas}
          esCliente={esCliente}
          empresaVisible={!esCliente}
          soloLectura={archivadas}
        />
      ) : null}

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
