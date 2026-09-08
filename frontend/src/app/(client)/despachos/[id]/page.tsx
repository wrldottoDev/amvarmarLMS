"use client";

import { ArrowLeft, Ban, Check, CircleCheckBig, PackageCheck, Truck, X } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { DocumentosDespacho } from "@/components/despachos/documentos-despacho";
import { InsigniaDespacho } from "@/components/despachos/insignia-despacho";
import { PasosDespacho } from "@/components/despachos/pasos-despacho";
import { BadgeEstado } from "@/components/shipments/badges-carga";
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
  useDespachar,
  useDespacho,
  usePreparar,
  useRechazar,
} from "@/features/despachos/consultas";
import { ErrorApi } from "@/lib/api/client";
import { formatearFechaHora } from "@/lib/utilidades";

export default function PaginaDetalleDespacho() {
  const parametros = useParams<{ id: string }>();
  const id = parametros.id;
  const { usuario } = useSesion();
  const { data, isPending, error } = useDespacho(id);

  const [cancelando, setCancelando] = useState(false);
  const [rechazando, setRechazando] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [motivoCancelacion, setMotivoCancelacion] = useState("");

  const aprobar = useAprobar(id);
  const rechazar = useRechazar(id);
  const preparar = usePreparar(id);
  const despachar = useDespachar(id);
  const completar = useCompletar(id);
  const cancelar = useCancelar(id);

  if (isPending) return <CargandoPagina />;
  if (error) return <AvisoError error={error} />;
  if (!data) return null;

  const esCliente = Boolean(usuario?.empresa);
  const puedeCancelarCliente = esCliente && clienteCancelaDesde.includes(data.status);
  const puedeCancelarOperaciones =
    !esCliente && ["PENDING", "APPROVED", "PREPARING"].includes(data.status);
  const enCurso =
    aprobar.isPending || preparar.isPending || despachar.isPending || completar.isPending;
  const fallo =
    aprobar.error ??
    rechazar.error ??
    preparar.error ??
    despachar.error ??
    completar.error ??
    cancelar.error;
  const conflictoVersion = fallo instanceof ErrorApi && fallo.code === "DISPATCH_VERSION_CONFLICT";

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

      <div className="rounded-md border bg-[var(--superficie)] px-4 py-5">
        <PasosDespacho estado={data.status} />
        <p className="mt-4 text-center text-sm text-[var(--texto-secundario)]">
          {explicacionDespacho[data.status] ?? ""}
        </p>
      </div>

      {data.rejected_reason ? (
        <div className="rounded-md border border-[var(--peligro-borde)] bg-[var(--peligro-tenue)] px-4 py-3 text-sm text-[var(--peligro)]">
          <strong className="block">Motivo</strong>
          <p className="mt-0.5">{data.rejected_reason}</p>
        </div>
      ) : null}

      {fallo ? <AvisoError error={fallo} /> : null}
      {conflictoVersion ? (
        <p className="bg-[var(--advertencia-tenue)] px-4 py-3 text-sm text-[var(--advertencia)]">
          Otra persona modificó este despacho. Se actualizó con los datos más recientes; revisá el
          estado antes de reintentar.
        </p>
      ) : null}

      {!esCliente ? (
        <div className="flex flex-wrap gap-2 rounded-md border bg-[var(--superficie)] px-4 py-4">
          {data.status === "PENDING" ? (
            <>
              <Boton
                onClick={() => aprobar.mutate({ rowVersion: data.row_version })}
                cargando={aprobar.isPending}
              >
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
            <Boton onClick={() => preparar.mutate(data.row_version)} cargando={preparar.isPending}>
              <PackageCheck className="size-4" aria-hidden="true" />
              Marcar en preparación
            </Boton>
          ) : null}

          {data.status === "PREPARING" ? (
            <Boton onClick={() => despachar.mutate(data.row_version)} cargando={despachar.isPending}>
              <Truck className="size-4" aria-hidden="true" />
              Registrar salida
            </Boton>
          ) : null}

          {data.status === "DISPATCHED" ? (
            <Boton onClick={() => completar.mutate(data.row_version)} cargando={completar.isPending}>
              <CircleCheckBig className="size-4" aria-hidden="true" />
              Completar despacho
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
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-[var(--superficie)] px-4 py-4">
          <p className="text-sm text-[var(--texto-secundario)]">
            Todavía podés cancelar esta solicitud.
          </p>
          <Boton variante="secundario" onClick={() => setCancelando(true)}>
            <Ban className="size-4" aria-hidden="true" />
            Cancelar solicitud
          </Boton>
        </div>
      ) : null}

      <section aria-labelledby="cargas-incluidas">
        <div className="mb-2 flex items-center justify-between gap-3">
          <h2 id="cargas-incluidas" className="text-base font-bold">
            Cargas incluidas
          </h2>
          <span className="text-sm text-[var(--texto-secundario)]">{data.shipment_count}</span>
        </div>
        <ul className="divide-y overflow-hidden rounded-md border bg-[var(--superficie)]">
          {data.shipments.map((carga) => (
            <li key={carga.id}>
              <Link
                href={`/shipments/${carga.id}`}
                className="flex flex-wrap items-center gap-3 px-4 py-3 hover:bg-[var(--hover)]"
              >
                <span className="min-w-0 flex-1">
                  <strong className="block truncate text-sm">
                    {carga.wr || carga.invoice || carga.shipment_number}
                  </strong>
                  <span className="block text-xs text-[var(--texto-secundario)]">
                    {carga.shipment_number} · {carga.package_count} piezas
                    {carga.weight_kg ? ` · ${carga.weight_kg} kg` : ""}
                  </span>
                </span>
                <BadgeEstado estado={carga.status} />
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <DocumentosDespacho dispatchId={id} />

      <dl className="grid gap-px overflow-hidden rounded-md border bg-[var(--borde)] sm:grid-cols-2">
        {[
          { termino: "Solicitado", valor: formatearFechaHora(data.requested_at) },
          { termino: "Aprobado", valor: formatearFechaHora(data.approved_at) },
          { termino: "Salida física", valor: formatearFechaHora(data.dispatched_at) },
          { termino: "Completado", valor: formatearFechaHora(data.completed_at) },
          { termino: "Dirección de entrega", valor: data.delivery_address || "La habitual" },
          { termino: "Instrucciones", valor: data.instructions || "Sin instrucciones" },
        ].map((dato) => (
          <div key={dato.termino} className="bg-[var(--superficie)] px-4 py-3">
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
          <label className="block">
            <span className="mb-1 block font-medium">Motivo de la cancelación</span>
            <textarea
              className="w-full rounded-md border px-3 py-2 text-sm"
              rows={2}
              maxLength={2000}
              value={motivoCancelacion}
              onChange={(evento) => setMotivoCancelacion(evento.target.value)}
            />
          </label>
          {cancelar.error ? <AvisoError error={cancelar.error} /> : null}
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              className="h-10 rounded-md border px-4 text-sm font-medium hover:bg-[var(--hover)]"
              onClick={() => setCancelando(false)}
            >
              No, volver
            </button>
            <Boton
              variante="peligro"
              cargando={cancelar.isPending}
              disabled={!motivoCancelacion.trim()}
              onClick={async () => {
                await cancelar.mutateAsync({
                  motivo: motivoCancelacion.trim(),
                  rowVersion: data.row_version,
                });
                setCancelando(false);
                setMotivoCancelacion("");
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
              className="h-10 rounded-md border px-4 text-sm font-medium hover:bg-[var(--hover)]"
              onClick={() => setRechazando(false)}
            >
              Volver
            </button>
            <Boton
              variante="peligro"
              disabled={!motivo.trim()}
              cargando={rechazar.isPending}
              onClick={async () => {
                await rechazar.mutateAsync({ motivo: motivo.trim(), rowVersion: data.row_version });
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
