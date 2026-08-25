"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, CalendarDays, MapPin, Package, Route, Ship, Tag } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { BadgeEstado, BadgePendientes } from "@/components/shipments/badges-carga";
import { TimelineCarga } from "@/components/shipments/timeline-carga";
import { TransicionCarga } from "@/components/shipments/transicion-carga";
import { AvisoError } from "@/components/ui/aviso-error";
import { Expediente } from "@/components/documentos/expediente";
import { EstadoExplicado } from "@/components/shipments/estado-explicado";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import { useSesion } from "@/features/auth/contexto-sesion";
import { api, exigirDatos } from "@/lib/api/client";
import { formatearFecha, formatearFechaHora } from "@/lib/utilidades";

function Dato({ etiqueta, valor }: { etiqueta: string; valor: React.ReactNode }) {
  return (
    <div className="min-w-0 py-4">
      <dt className="text-xs font-bold uppercase text-[var(--texto-secundario)]">{etiqueta}</dt>
      <dd className="mt-1.5 text-sm font-medium">{valor || "No registrado"}</dd>
    </div>
  );
}

export default function PaginaDetalleCarga() {
  const parametros = useParams<{ id: string }>();
  const cargaId = parametros.id;
  const { usuario } = useSesion();
  const consulta = useQuery({
    queryKey: ["carga", cargaId],
    queryFn: async () =>
      exigirDatos(
        await api.GET("/api/v1/shipments/{shipment_id}", {
          params: { path: { shipment_id: cargaId } },
        }),
      ),
  });

  if (consulta.isLoading) return <CargandoPagina texto="Cargando carga" />;
  if (consulta.error) return <AvisoError error={consulta.error} />;
  if (!consulta.data) return null;

  const carga = consulta.data;
  const pendientes = usuario?.empresa ? carga.client_action_required_count : carga.open_requirements_count;

  return (
    <div className="space-y-8">
      <header className="border-b pb-6">
        <Link href="/shipments" className="mb-5 inline-flex items-center gap-2 text-sm font-semibold text-[var(--mar)] hover:underline">
          <ArrowLeft className="size-4" aria-hidden="true" />
          Cargas
        </Link>
        <div className="flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <BadgeEstado estado={carga.status} />
              <BadgePendientes cantidad={pendientes} />
            </div>
            <h1 className="mt-3 text-2xl font-bold sm:text-3xl">{carga.invoice || carga.shipment_number}</h1>
            {carga.invoice ? <p className="mt-1 font-mono text-sm text-[var(--texto-secundario)]">{carga.shipment_number}</p> : null}
          </div>
          <TransicionCarga cargaId={carga.id} estado={carga.status} rowVersion={carga.row_version} />
        </div>

        <div className="mt-5">
          <EstadoExplicado
            estado={carga.status}
            esCliente={Boolean(usuario?.empresa)}
            puedeDespachar={pendientes === 0}
          />
        </div>
      </header>

      <Expediente cargaId={carga.id} esCliente={Boolean(usuario?.empresa)} />

      <section aria-labelledby="ruta-carga">
        <div className="mb-4 flex items-center gap-2">
          <Route className="size-5 text-[var(--marca)]" aria-hidden="true" />
          <h2 id="ruta-carga" className="text-base font-bold">Ruta</h2>
        </div>
        <div className="grid overflow-hidden rounded-lg border bg-white md:grid-cols-[1fr_auto_1fr]">
          <div className="p-5">
            <p className="text-xs font-bold uppercase text-[var(--texto-secundario)]">Origen</p>
            <p className="mt-2 text-lg font-semibold">{carga.origin.name}</p>
            <p className="mt-1 text-sm text-[var(--texto-secundario)]">{carga.origin.location_code} · {carga.origin.country_code}</p>
          </div>
          <div className="grid place-items-center border-y px-5 py-3 text-[var(--mar)] md:border-x md:border-y-0">
            <ArrowRight className="size-5 rotate-90 md:rotate-0" aria-hidden="true" />
          </div>
          <div className="p-5">
            <p className="text-xs font-bold uppercase text-[var(--texto-secundario)]">Destino</p>
            <p className="mt-2 text-lg font-semibold">{carga.destination.name}</p>
            <p className="mt-1 text-sm text-[var(--texto-secundario)]">{carga.destination.location_code} · {carga.destination.country_code}</p>
          </div>
        </div>
      </section>

      <section className="grid gap-8 xl:grid-cols-2">
        <div>
          <h2 className="border-b pb-3 text-base font-bold">Datos de la carga</h2>
          <dl className="grid grid-cols-2 divide-x border-b">
            <div className="pr-5">
              <Dato etiqueta="ETA" valor={<span className="inline-flex items-center gap-2"><CalendarDays className="size-4 text-[var(--marca)]" />{formatearFecha(carga.estimated_arrival_at)}</span>} />
              <Dato etiqueta="Ubicación actual" valor={<span className="inline-flex items-center gap-2"><MapPin className="size-4 text-[var(--marca)]" />{carga.current_location ?? "No registrada"}</span>} />
              <Dato etiqueta="Dirección de destino" valor={carga.destination_address} />
            </div>
            <div className="pl-5">
              <Dato etiqueta="Modo de transporte" valor={<span className="inline-flex items-center gap-2"><Ship className="size-4 text-[var(--marca)]" />{carga.transport_mode ?? "No registrado"}</span>} />
              <Dato etiqueta="Paquetes" valor={<span className="inline-flex items-center gap-2"><Package className="size-4 text-[var(--marca)]" />{carga.package_count}</span>} />
              <Dato etiqueta="Factura" valor={<span className="inline-flex items-center gap-2"><Tag className="size-4 text-[var(--marca)]" />{carga.invoice ?? "No registrada"}</span>} />
            </div>
          </dl>
          {carga.description ? <p className="mt-4 text-sm leading-6 text-[var(--texto-secundario)]">{carga.description}</p> : null}
        </div>

        <div>
          <h2 className="border-b pb-3 text-base font-bold">Hitos logísticos</h2>
          <dl className="grid grid-cols-2 gap-x-6 border-b">
            <Dato etiqueta="Recibida" valor={formatearFechaHora(carga.received_at)} />
            <Dato etiqueta="Almacenada" valor={formatearFechaHora(carga.stored_at)} />
            <Dato etiqueta="Despachada" valor={formatearFechaHora(carga.dispatched_at)} />
            <Dato etiqueta="Entregada" valor={formatearFechaHora(carga.delivered_at)} />
          </dl>
          <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-xs text-[var(--texto-secundario)]">
            <span>Creada {formatearFechaHora(carga.created_at)}</span>
            <span>Actualizada {formatearFechaHora(carga.updated_at)}</span>
            <span>Versión {carga.row_version}</span>
          </div>
        </div>
      </section>

      <section className="border-t pt-7" aria-labelledby="historial-carga">
        <h2 id="historial-carga" className="mb-6 text-base font-bold">Línea de tiempo</h2>
        <TimelineCarga cargaId={carga.id} />
      </section>
    </div>
  );
}
