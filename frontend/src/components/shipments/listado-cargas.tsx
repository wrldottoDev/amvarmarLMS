import { ArrowRight, MapPin } from "lucide-react";
import Link from "next/link";
import { BadgeEstado, BadgePendientes } from "./badges-carga";
import type { CargaResumen } from "@/lib/api/tipos";
import { formatearFecha } from "@/lib/utilidades";

function referencia(carga: CargaResumen) {
  return carga.invoice || carga.shipment_number;
}

function cantidadPendiente(carga: CargaResumen, esCliente: boolean) {
  return esCliente ? carga.client_action_required_count : carga.open_requirements_count;
}

export function ListadoCargas({ cargas, esCliente }: { cargas: CargaResumen[]; esCliente: boolean }) {
  return (
    <>
      <div className="hidden overflow-hidden rounded-lg border bg-white md:block">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] border-collapse text-left">
            <thead className="border-b bg-[#f8f9fa] text-xs font-bold uppercase text-[var(--texto-secundario)]">
              <tr>
                <th className="px-5 py-3">Carga</th>
                <th className="px-5 py-3">Ruta</th>
                <th className="px-5 py-3">ETA</th>
                <th className="px-5 py-3">Estado</th>
                <th className="px-5 py-3">Pendientes</th>
                <th className="w-14 px-4 py-3"><span className="sr-only">Ver</span></th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {cargas.map((carga) => (
                <tr key={carga.id} className="group hover:bg-[#f8fafb]">
                  <td className="px-5 py-4">
                    <Link href={`/shipments/${carga.id}`} className="font-semibold text-[var(--mar)] hover:underline">
                      {referencia(carga)}
                    </Link>
                    {carga.invoice ? <p className="mt-1 text-xs text-[var(--texto-secundario)]">{carga.shipment_number}</p> : null}
                  </td>
                  <td className="px-5 py-4">
                    <div className="flex items-center gap-2 text-sm">
                      <span>{carga.origin.location_code}</span>
                      <ArrowRight className="size-3.5 text-[#879399]" aria-hidden="true" />
                      <span>{carga.destination.location_code}</span>
                    </div>
                    <p className="mt-1 max-w-60 truncate text-xs text-[var(--texto-secundario)]">
                      {carga.origin.name} · {carga.destination.name}
                    </p>
                  </td>
                  <td className="px-5 py-4 text-sm">{formatearFecha(carga.estimated_arrival_at)}</td>
                  <td className="px-5 py-4"><BadgeEstado estado={carga.status} /></td>
                  <td className="px-5 py-4"><BadgePendientes cantidad={cantidadPendiente(carga, esCliente)} /></td>
                  <td className="px-4 py-4">
                    <Link
                      href={`/shipments/${carga.id}`}
                      className="grid size-9 place-items-center rounded-md text-[var(--mar)] opacity-60 hover:bg-[#e8f0f2] group-hover:opacity-100"
                      title="Ver carga"
                      aria-label={`Ver carga ${referencia(carga)}`}
                    >
                      <ArrowRight className="size-4" />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid gap-3 md:hidden">
        {cargas.map((carga) => (
          <Link key={carga.id} href={`/shipments/${carga.id}`} className="rounded-lg border bg-white p-4 shadow-sm active:bg-[#f8fafb]">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate font-semibold text-[var(--mar)]">{referencia(carga)}</p>
                {carga.invoice ? <p className="mt-0.5 truncate text-xs text-[var(--texto-secundario)]">{carga.shipment_number}</p> : null}
              </div>
              <BadgeEstado estado={carga.status} />
            </div>
            <div className="mt-4 flex items-center gap-2 text-sm">
              <MapPin className="size-4 shrink-0 text-[var(--marca)]" aria-hidden="true" />
              <span>{carga.origin.location_code}</span>
              <ArrowRight className="size-3.5 text-[#879399]" aria-hidden="true" />
              <span>{carga.destination.location_code}</span>
            </div>
            <div className="mt-4 flex items-center justify-between gap-3 border-t pt-3">
              <span className="text-xs text-[var(--texto-secundario)]">ETA {formatearFecha(carga.estimated_arrival_at)}</span>
              <BadgePendientes cantidad={cantidadPendiente(carga, esCliente)} />
            </div>
          </Link>
        ))}
      </div>
    </>
  );
}
