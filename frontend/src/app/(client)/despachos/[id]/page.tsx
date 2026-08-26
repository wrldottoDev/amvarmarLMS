"use client";

import { ArrowLeft, Ban, Check, PackageCheck, Truck, X } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { DocumentosDespacho } from "@/components/despachos/documentos-despacho";
import { InsigniaDespacho } from "@/components/despachos/insignia-despacho";
import { PasosDespacho } from "@/components/despachos/pasos-despacho";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import { Modal } from "@/components/ui/modal";
import { useSesion } from "@/features/auth/contexto-sesion";
import { clienteCancelaDesde, explicacionDespacho, metodos } from "@/features/despachos/catalogo";
import {
  useAprobar,
  useCancelar,
  useCompletar,
  useDespacho,
  usePreparar,
  useRechazar,
} from "@/features/despachos/consultas";
import { formatearFechaHora } from "@/lib/utilidades";

export default function PaginaDetalleDespacho() {
  const parametros = useParams<{ id: string }>();
  const id = parametros.id;
  const { usuario } = useSesion();
  const { data, isPending, error } = useDespacho(id);

  const [cancelando, setCancelando] = useState(false);
  const [rechazando, setRechazando] = useState(false);
  const [motivo, setMotivo] = useState("");

  const aprobar = useAprobar(id);
  const rechazar = useRechazar(id);
  const preparar = usePreparar(id);
  const completar = useCompletar(id);
  const cancelar = useCancelar(id);

  if (isPending) return <CargandoPagina />;
  if (error) return <AvisoError error={error} />;
  if (!data) return null;

  const esCliente = Boolean(usuario?.empresa);
  const puedeCancelarCliente = esCliente && clienteCancelaDesde.includes(data.status);
  const puedeCancelarOperaciones =
    !esCliente && ["PENDING", "APPROVED", "PREPARING"].includes(data.status);
  const enCurso = aprobar.isPending || preparar.isPending || completar.isPending;
  const fallo = aprobar.error ?? preparar.error ?? completar.error ?? cancelar.error;

  return (
    <section className="mx-auto max-w-3xl space-y-5">
      <Link
        href="/despachos"
        className="inline-flex items-center gap-1.5 text-sm text-[var(--texto-secundario)] hover:underline"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        Volver a despachos
      </Link>

      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">{data.dispatch_number}</h1>
          <p className="mt-0.5 text-sm text-[var(--texto-secundario)]">
            {data.shipment_count === 1 ? "1 carga" : `${data.shipment_count} cargas`} ·{" "}
            {metodos.find((m) => m.valor === data.method)?.etiqueta ?? data.method}
          </p>
        </div>
        <InsigniaDespacho estado={data.status} />
      </header>

      <div className="rounded-md border bg-white px-4 py-5">
        <PasosDespacho estado={data.status} />
        <p className="mt-4 text-center text-sm text-[var(--texto-secundario)]">
          {explicacionDespacho[data.status] ?? ""}
        </p>
      </div>

      {data.rejected_reason ? (
        <div className="rounded-md border border-[#f0b8b3] bg-[#fff2f0] px-4 py-3 text-sm text-[#82231b]">
          <strong className="block">Motivo</strong>
          <p className="mt-0.5">{data.rejected_reason}</p>
        </div>
      ) : null}

      {fallo ? <AvisoError error={fallo} /> : null}

      {!esCliente ? (
        <div className="flex flex-wrap gap-2 rounded-md border bg-white px-4 py-4">
          {data.status === "PENDING" ? (
            <>
              <Boton onClick={() => aprobar.mutate(undefined)} cargando={aprobar.isPending}>
                <Check className="size-4" aria-hidden="true" />
                Aprobar
              </Boton>
              <Boton variante="peligro" onClick={() => setRechazando(true)} disabled={enCurso}>
                <X className="size-4" aria-hidden="true" />
                Rechazar
              </Boton>
            </>
          ) : null}

          {data.status === "APPROVED" ? (
            <Boton onClick={() => preparar.mutate()} cargando={preparar.isPending}>
              <PackageCheck className="size-4" aria-hidden="true" />
              Marcar en preparación
            </Boton>
          ) : null}

          {data.status === "PREPARING" ? (
            <Boton onClick={() => completar.mutate()} cargando={completar.isPending}>
              <Truck className="size-4" aria-hidden="true" />
              Confirmar despacho
            </Boton>
          ) : null}

          {puedeCancelarOperaciones ? (
            <Boton variante="secundario" onClick={() => setCancelando(true)} disabled={enCurso}>
              <Ban className="size-4" aria-hidden="true" />
              Cancelar
            </Boton>
          ) : null}
        </div>
      ) : null}

      {puedeCancelarCliente ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-white px-4 py-4">
          <p className="text-sm text-[var(--texto-secundario)]">
            Todavía podés cancelar esta solicitud.
          </p>
          <Boton variante="secundario" onClick={() => setCancelando(true)}>
            <Ban className="size-4" aria-hidden="true" />
            Cancelar solicitud
          </Boton>
        </div>
      ) : null}

      <DocumentosDespacho dispatchId={id} esCliente={esCliente} />

      <dl className="grid gap-px overflow-hidden rounded-md border bg-[#e4eaec] sm:grid-cols-2">
        {[
          { termino: "Solicitado", valor: formatearFechaHora(data.requested_at) },
          { termino: "Aprobado", valor: formatearFechaHora(data.approved_at) },
          { termino: "Despachado", valor: formatearFechaHora(data.completed_at) },
          { termino: "Dirección de entrega", valor: data.delivery_address || "La habitual" },
          { termino: "Instrucciones", valor: data.instructions || "Sin instrucciones" },
        ].map((dato) => (
          <div key={dato.termino} className="bg-white px-4 py-3">
            <dt className="text-xs text-[var(--texto-secundario)]">{dato.termino}</dt>
            <dd className="mt-0.5 text-sm">{dato.valor}</dd>
          </div>
        ))}
      </dl>

      <Modal abierto={cancelando} titulo="¿Cancelar la solicitud?" cerrar={() => setCancelando(false)}>
        <div className="space-y-3 text-sm">
          <p>
            Las cargas vuelven a quedar almacenadas y podés solicitar el despacho de nuevo cuando
            quieras.
          </p>
          {cancelar.error ? <AvisoError error={cancelar.error} /> : null}
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              className="h-10 rounded-md border px-4 text-sm font-medium hover:bg-[#edf1f2]"
              onClick={() => setCancelando(false)}
            >
              No, volver
            </button>
            <Boton
              variante="peligro"
              cargando={cancelar.isPending}
              onClick={async () => {
                await cancelar.mutateAsync(undefined);
                setCancelando(false);
              }}
            >
              Sí, cancelar
            </Boton>
          </div>
        </div>
      </Modal>

      <Modal abierto={rechazando} titulo="Rechazar la solicitud" cerrar={() => setRechazando(false)}>
        <div className="space-y-3 text-sm">
          <label className="block">
            <span className="mb-1 block font-medium">
              ¿Por qué se rechaza? El cliente va a ver esto.
            </span>
            <textarea
              className="w-full rounded-md border px-3 py-2 text-sm"
              rows={3}
              value={motivo}
              onChange={(evento) => setMotivo(evento.target.value)}
              placeholder="Falta la factura comercial de dos cargas"
            />
          </label>
          {rechazar.error ? <AvisoError error={rechazar.error} /> : null}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              className="h-10 rounded-md border px-4 text-sm font-medium hover:bg-[#edf1f2]"
              onClick={() => setRechazando(false)}
            >
              Volver
            </button>
            <Boton
              variante="peligro"
              disabled={!motivo.trim()}
              cargando={rechazar.isPending}
              onClick={async () => {
                await rechazar.mutateAsync(motivo.trim());
                setRechazando(false);
                setMotivo("");
              }}
            >
              Rechazar
            </Boton>
          </div>
        </div>
      </Modal>
    </section>
  );
}
