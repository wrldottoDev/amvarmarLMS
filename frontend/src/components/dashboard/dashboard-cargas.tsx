"use client";

import { useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  RefreshCw,
  Ship,
  Warehouse,
} from "lucide-react";
import Link from "next/link";
import { BadgeEstado, BadgePendientes } from "@/components/shipments/badges-carga";
import { AvisoError } from "@/components/ui/aviso-error";
import { CargandoPagina, EstadoVacio } from "@/components/ui/estados-pagina";
import { api, exigirDatos } from "@/lib/api/client";
import { formatearFecha } from "@/lib/utilidades";

type VistaDashboard = "client" | "operations";

const tarjetas = [
  { clave: "en_bodega", etiqueta: "En bodega", icono: Warehouse, estilo: "bg-[var(--marca-tenue)] text-[var(--mar)]" },
  { clave: "en_transito", etiqueta: "En tránsito", icono: Ship, estilo: "bg-[var(--marca-tenue)] text-[var(--marca-oscura)]" },
  { clave: "proximos_a_llegar", etiqueta: "Próximos a llegar", icono: CalendarClock, estilo: "bg-[var(--marca-tenue)] text-[var(--marca-oscura)]" },
  { clave: "requieren_accion", etiqueta: "Requieren acción", icono: AlertCircle, estilo: "bg-[var(--advertencia-tenue)] text-[var(--advertencia)]" },
  { clave: "entregados_este_mes", etiqueta: "Entregados este mes", icono: CheckCircle2, estilo: "bg-[var(--exito-tenue)] text-[var(--exito)]" },
] as const;

export function DashboardCargas({ vista, titulo }: { vista: VistaDashboard; titulo: string }) {
  const consulta = useQuery({
    queryKey: ["dashboard", vista],
    queryFn: async () =>
      vista === "client"
        ? exigirDatos(await api.GET("/api/v1/dashboard/client"))
        : exigirDatos(await api.GET("/api/v1/dashboard/operations")),
  });

  if (consulta.isLoading) return <CargandoPagina texto="Cargando resumen" />;
  if (consulta.error) return <AvisoError error={consulta.error} />;
  if (!consulta.data) return null;

  return (
    <div className="space-y-8">
      <header className="flex items-end justify-between gap-4 border-b pb-5">
        <div>
          <p className="text-xs font-bold uppercase text-[var(--marca)]">Vista general</p>
          <h1 className="mt-1 text-2xl font-bold">{titulo}</h1>
        </div>
        <button
          type="button"
          className="grid size-10 place-items-center rounded-md border bg-[var(--superficie)] text-[var(--texto-secundario)] hover:bg-[var(--hover)]"
          onClick={() => void consulta.refetch()}
          title="Actualizar"
          aria-label="Actualizar dashboard"
        >
          <RefreshCw className={`size-4 ${consulta.isFetching ? "animate-spin" : ""}`} />
        </button>
      </header>

      <section className="grid grid-cols-2 gap-3 xl:grid-cols-5" aria-label="Indicadores de cargas">
        {tarjetas.map((tarjeta) => {
          const Icono = tarjeta.icono;
          return (
            <article key={tarjeta.clave} className="min-h-32 rounded-lg border bg-[var(--superficie)] p-4 shadow-sm last:col-span-2 xl:last:col-span-1">
              <div className="flex items-start justify-between gap-3">
                <span className={`grid size-9 place-items-center rounded-md ${tarjeta.estilo}`}>
                  <Icono className="size-4" aria-hidden="true" />
                </span>
                <strong className="text-2xl font-bold tabular-nums">{consulta.data.tarjetas[tarjeta.clave]}</strong>
              </div>
              <p className="mt-5 text-sm font-medium text-[var(--texto-secundario)]">{tarjeta.etiqueta}</p>
            </article>
          );
        })}
      </section>

      <section aria-labelledby="proximos-movimientos">
        <div className="mb-4 flex items-center justify-between gap-4">
          <div>
            <h2 id="proximos-movimientos" className="text-base font-bold">Próximos movimientos</h2>
            <p className="mt-1 text-sm text-[var(--texto-secundario)]">{consulta.data.proximos_movimientos.length} cargas</p>
          </div>
          <Link href="/shipments" className="inline-flex items-center gap-1.5 text-sm font-semibold text-[var(--mar)] hover:underline">
            Ver cargas <ArrowRight className="size-4" />
          </Link>
        </div>

        {!consulta.data.proximos_movimientos.length ? (
          <div className="rounded-lg border bg-[var(--superficie)]">
            <EstadoVacio titulo="Sin movimientos próximos" descripcion="No hay cargas programadas en este momento." />
          </div>
        ) : (
          <div className="divide-y rounded-lg border bg-[var(--superficie)]">
            {consulta.data.proximos_movimientos.map((carga) => {
              const pendientes = vista === "client" ? carga.client_action_required_count : carga.open_requirements_count;
              return (
                <Link
                  key={carga.id}
                  href={`/shipments/${carga.id}`}
                  className="grid gap-4 p-4 hover:bg-[var(--hover)] sm:grid-cols-[minmax(160px,1fr)_minmax(180px,1.25fr)_120px_auto_32px] sm:items-center"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-[var(--mar)]">{carga.invoice || carga.shipment_number}</p>
                    {carga.invoice ? <p className="mt-1 truncate font-mono text-[11px] text-[var(--texto-secundario)]">{carga.shipment_number}</p> : null}
                  </div>
                  <div className="min-w-0 text-sm">
                    <p className="truncate">{carga.destination.name}, {carga.destination.country_code}</p>
                    <p className="mt-1 text-xs text-[var(--texto-secundario)]">Desde {carga.origin.name}</p>
                  </div>
                  <p className="text-sm"><span className="mr-1 text-xs text-[var(--texto-secundario)]">ETA</span>{formatearFecha(carga.estimated_arrival_at)}</p>
                  <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                    <BadgeEstado estado={carga.status} />
                    <BadgePendientes cantidad={pendientes} />
                  </div>
                  <ArrowRight className="hidden size-4 text-[var(--texto-secundario)] sm:block" aria-hidden="true" />
                </Link>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
