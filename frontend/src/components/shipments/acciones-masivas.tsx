"use client";

import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { Check, RefreshCw, X } from "lucide-react";
import { useMemo, useState } from "react";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { Modal } from "@/components/ui/modal";
import { etiquetaEstado, esEstadoCarga } from "@/features/shipments/catalogo-estados";
import { api, ErrorApi, exigirDatos } from "@/lib/api/client";
import type { CargaResumen, EstadoCarga } from "@/lib/api/tipos";

function nombreEstado(estado: string) {
  return esEstadoCarga(estado) ? etiquetaEstado[estado] : estado;
}

export function AccionesMasivas({
  cargas,
  limpiar,
}: {
  cargas: CargaResumen[];
  limpiar: () => void;
}) {
  const [abierto, setAbierto] = useState(false);
  const [destino, setDestino] = useState<EstadoCarga | "">("");
  const [motivo, setMotivo] = useState("");
  const [confirmado, setConfirmado] = useState(false);
  const cliente = useQueryClient();
  const consultas = useQueries({
    queries: cargas.map((carga) => ({
      queryKey: ["transiciones-disponibles", carga.id, carga.status, carga.row_version],
      queryFn: async () =>
        exigirDatos(
          await api.GET("/api/v1/shipments/{shipment_id}/transitions/available", {
            params: { path: { shipment_id: carga.id } },
          }),
        ),
      enabled: abierto,
    })),
  });

  const opciones = useMemo(() => {
    if (!cargas.length || consultas.some((consulta) => !consulta.data)) return [];
    const primera = consultas[0]?.data ?? [];
    return primera.filter(
      (opcion) =>
        !opcion.blocked &&
        consultas.every((consulta) =>
          consulta.data?.some(
            (otra) => otra.to_status === opcion.to_status && !otra.blocked,
          ),
        ),
    );
  }, [cargas.length, consultas]);

  const seleccion = opciones.find((opcion) => opcion.to_status === destino);
  const requiereMotivo = Boolean(
    seleccion &&
      consultas.some((consulta) =>
        consulta.data?.some(
          (opcion) => opcion.to_status === destino && opcion.requires_reason,
        ),
      ),
  );
  const mutacion = useMutation({
    mutationFn: async () => {
      if (!destino || !esEstadoCarga(destino)) throw new Error("Seleccioná el estado destino.");
      return exigirDatos(
        await api.POST("/api/v1/shipments/bulk-transition", {
          body: {
            shipments: cargas.map((carga) => ({ id: carga.id, row_version: carga.row_version })),
            to_status: destino,
            note: motivo.trim() || undefined,
          },
        }),
      );
    },
    onSuccess: async () => {
      await Promise.all([
        cliente.invalidateQueries({ queryKey: ["cargas"] }),
        cliente.invalidateQueries({ queryKey: ["dashboard"] }),
      ]);
      limpiar();
      cerrar();
    },
  });

  function cerrar() {
    setAbierto(false);
    setDestino("");
    setMotivo("");
    setConfirmado(false);
    mutacion.reset();
  }

  const detalleError =
    mutacion.error instanceof ErrorApi && Array.isArray(mutacion.error.details)
      ? mutacion.error.details[0]
      : undefined;

  return (
    <>
      <div className="sticky bottom-4 z-20 flex flex-wrap items-center gap-3 rounded-lg border bg-[var(--superficie)] px-4 py-3 shadow-lg" role="region" aria-label="Acciones masivas">
        <strong className="text-sm">{cargas.length} cargas seleccionadas</strong>
        <Boton onClick={() => setAbierto(true)}>
          <RefreshCw className="size-4" aria-hidden="true" /> Cambiar estado
        </Boton>
        <button type="button" className="ml-auto flex items-center gap-1 text-sm text-[var(--texto-secundario)] hover:underline" onClick={limpiar}>
          <X className="size-4" aria-hidden="true" /> Limpiar selección
        </button>
      </div>

      <Modal abierto={abierto} cerrar={cerrar} titulo="Cambiar estado de varias cargas">
        <div className="space-y-4 p-5">
          <p className="text-sm">Se actualizarán {cargas.length} cargas en una sola operación.</p>
          {consultas.some((consulta) => consulta.isLoading) ? <p className="text-sm">Validando estados disponibles…</p> : null}
          {consultas.some((consulta) => consulta.error) ? <AvisoError error={consultas.find((consulta) => consulta.error)?.error} /> : null}
          {!consultas.some((consulta) => consulta.isLoading) && opciones.length === 0 ? (
            <p className="rounded-md bg-[var(--advertencia-tenue)] p-3 text-sm">Las cargas seleccionadas no tienen un estado destino válido en común.</p>
          ) : null}
          {opciones.length ? (
            <label className="block">
              <span className="mb-1 block text-sm font-medium">Estado destino</span>
              <select className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm" value={destino} onChange={(evento) => { setDestino(evento.target.value as EstadoCarga); setConfirmado(false); }}>
                <option value="">Seleccionar…</option>
                {opciones.map((opcion) => <option key={opcion.to_status} value={opcion.to_status}>{nombreEstado(opcion.to_status)}</option>)}
              </select>
            </label>
          ) : null}
          {destino ? (
            <label className="block">
              <span className="mb-1 block text-sm font-medium">Motivo {requiereMotivo ? "(obligatorio)" : "(opcional)"}</span>
              <textarea className="min-h-24 w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm" value={motivo} maxLength={2000} onChange={(evento) => setMotivo(evento.target.value)} />
            </label>
          ) : null}
          {destino ? (
            <label className="flex items-start gap-2 text-sm">
              <input className="mt-0.5 size-4 accent-[var(--mar)]" type="checkbox" checked={confirmado} onChange={(evento) => setConfirmado(evento.target.checked)} />
              Confirmo cambiar {cargas.length} cargas a {nombreEstado(destino)}.
            </label>
          ) : null}
          {mutacion.error ? <AvisoError error={mutacion.error} /> : null}
          {detalleError && typeof detalleError === "object" && "shipment_id" in detalleError ? (
            <p className="text-xs text-[var(--peligro)]">Carga: {String(detalleError.shipment_id)}{"message" in detalleError ? ` · ${String(detalleError.message)}` : ""}</p>
          ) : null}
          <div className="flex justify-end gap-2">
            <Boton variante="secundario" onClick={cerrar}>Cancelar</Boton>
            <Boton cargando={mutacion.isPending} disabled={!destino || !confirmado || (requiereMotivo && !motivo.trim())} onClick={() => mutacion.mutate()}>
              <Check className="size-4" aria-hidden="true" /> Aplicar cambio
            </Boton>
          </div>
        </div>
      </Modal>
    </>
  );
}
