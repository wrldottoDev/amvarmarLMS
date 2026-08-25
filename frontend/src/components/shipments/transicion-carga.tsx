"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Check, RefreshCw } from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";
import { BadgeEstado } from "./badges-carga";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { AreaTexto, Campo } from "@/components/ui/campo";
import { Modal } from "@/components/ui/modal";
import { useSesion } from "@/features/auth/contexto-sesion";
import { esEstadoCarga, etiquetaEstado } from "@/features/shipments/catalogo-estados";
import { api, ErrorApi, exigirDatos } from "@/lib/api/client";
import type { EstadoCarga } from "@/lib/api/tipos";

interface DefinicionTransicion {
  desde: EstadoCarga;
  hacia: EstadoCarga;
  permiso: string;
  requiereMotivo: boolean;
}

const flujo: readonly EstadoCarga[] = [
  "PRE_ALERT",
  "IN_TRANSIT",
  "RECEIVED",
  "STORED",
  "DISPATCH_REQUESTED",
  "PREPARING",
  "DISPATCHED",
  "DELIVERED",
];

function construirTransiciones() {
  const resultado: DefinicionTransicion[] = [];
  flujo.slice(0, -1).forEach((desde, indice) => {
    const hacia = flujo[indice + 1];
    resultado.push({ desde, hacia, permiso: "shipments.transition.forward", requiereMotivo: false });
    resultado.push({
      desde: hacia,
      hacia: desde,
      permiso: hacia === "DELIVERED" ? "shipments.transition.revert_delivered" : "shipments.transition.backward",
      requiereMotivo: true,
    });
  });
  resultado.push({ desde: "PRE_ALERT", hacia: "CANCELLED", permiso: "shipments.cancel.prealert", requiereMotivo: true });
  resultado.push({ desde: "IN_TRANSIT", hacia: "CANCELLED", permiso: "shipments.cancel.in_transit", requiereMotivo: true });
  resultado.push({ desde: "CANCELLED", hacia: "PRE_ALERT", permiso: "shipments.reopen", requiereMotivo: true });
  resultado.push({ desde: "CANCELLED", hacia: "IN_TRANSIT", permiso: "shipments.reopen", requiereMotivo: true });
  return resultado;
}

const transiciones = construirTransiciones();

function permitidasDesdeDetalles(error: ErrorApi) {
  if (error.code !== "SHIPMENT_TRANSITION_INVALID" || !Array.isArray(error.details)) return [];
  const primerDetalle = error.details[0];
  if (!primerDetalle || typeof primerDetalle !== "object" || !("allowed" in primerDetalle)) return [];
  const allowed = primerDetalle.allowed;
  return Array.isArray(allowed) ? allowed.filter((valor): valor is string => typeof valor === "string") : [];
}

export function TransicionCarga({ cargaId, estado, rowVersion }: { cargaId: string; estado: string; rowVersion: number }) {
  const [abierto, setAbierto] = useState(false);
  const [destino, setDestino] = useState<EstadoCarga | "">("");
  const [motivo, setMotivo] = useState("");
  const [ubicacion, setUbicacion] = useState("");
  const [fecha, setFecha] = useState("");
  const [confirmarDespacho, setConfirmarDespacho] = useState(false);
  const [intentoEnviar, setIntentoEnviar] = useState(false);
  const { usuario } = useSesion();
  const queryClient = useQueryClient();

  const opciones = useMemo(
    () =>
      esEstadoCarga(estado)
        ? transiciones
            .filter((transicion) => transicion.desde === estado && usuario?.permisos.includes(transicion.permiso))
            .sort((a, b) => Number(a.requiereMotivo) - Number(b.requiereMotivo))
        : [],
    [estado, usuario?.permisos],
  );

  const seleccion = opciones.find((opcion) => opcion.hacia === destino);

  const mutacion = useMutation({
    mutationFn: async () => {
      if (!destino) throw new Error("Seleccioná el nuevo estado.");
      return exigirDatos(
        await api.POST("/api/v1/shipments/{shipment_id}/transitions", {
          params: { path: { shipment_id: cargaId } },
          body: {
            to_status: destino,
            row_version: rowVersion,
            note: motivo.trim() || undefined,
            location: ubicacion.trim() || undefined,
            occurred_at: fecha ? new Date(fecha).toISOString() : undefined,
          },
        }),
      );
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["carga", cargaId] }),
        queryClient.invalidateQueries({ queryKey: ["cargas"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ]);
      cerrar();
    },
    onError: async (error) => {
      if (error instanceof ErrorApi && error.code === "SHIPMENT_VERSION_CONFLICT") {
        await queryClient.invalidateQueries({ queryKey: ["carga", cargaId] });
      }
    },
  });

  function abrir() {
    setDestino(opciones[0]?.hacia ?? "");
    setAbierto(true);
  }

  function cerrar() {
    setAbierto(false);
    setConfirmarDespacho(false);
    setMotivo("");
    setUbicacion("");
    setFecha("");
    setIntentoEnviar(false);
    mutacion.reset();
  }

  function enviar(evento: FormEvent) {
    evento.preventDefault();
    setIntentoEnviar(true);
    if (seleccion?.requiereMotivo && !motivo.trim()) return;
    if (estado === "PREPARING" && destino === "DISPATCHED") {
      setConfirmarDespacho(true);
      return;
    }
    mutacion.mutate();
  }

  if (!opciones.length) return null;

  const permitidas = mutacion.error instanceof ErrorApi ? permitidasDesdeDetalles(mutacion.error) : [];
  const conflictoVersion = mutacion.error instanceof ErrorApi && mutacion.error.code === "SHIPMENT_VERSION_CONFLICT";

  return (
    <>
      <Boton onClick={abrir}>
        <RefreshCw className="size-4" aria-hidden="true" />
        Cambiar estado
      </Boton>

      <Modal abierto={abierto} cerrar={cerrar} titulo={confirmarDespacho ? "Confirmar despacho" : "Cambiar estado"}>
        {confirmarDespacho ? (
          <div className="space-y-5 p-5">
            <p className="text-sm font-medium">¿Está seguro de que desea despachar esta carga?</p>
            <div className="flex items-center gap-3 rounded-md bg-[#f4f6f7] p-4">
              <BadgeEstado estado={estado} />
              <ArrowRight className="size-4 text-[var(--texto-secundario)]" />
              <BadgeEstado estado="DISPATCHED" />
            </div>
            {mutacion.error ? <AvisoError error={mutacion.error} /> : null}
            <div className="flex justify-end gap-3">
              <Boton variante="secundario" onClick={() => setConfirmarDespacho(false)}>Volver</Boton>
              <Boton cargando={mutacion.isPending} onClick={() => mutacion.mutate()}>
                <Check className="size-4" /> Confirmar despacho
              </Boton>
            </div>
          </div>
        ) : (
          <form className="grid gap-5 p-5" onSubmit={enviar}>
            {mutacion.error ? <AvisoError error={mutacion.error} /> : null}
            {conflictoVersion ? (
              <p className="rounded-md bg-[#fff8e9] px-4 py-3 text-sm text-[#86520a]">Alguien más modificó esta carga. Los datos ya se están recargando.</p>
            ) : null}
            {permitidas.length ? (
              <p className="text-xs text-[var(--texto-secundario)]">Transiciones permitidas por el servidor: {permitidas.map((valor) => esEstadoCarga(valor) ? etiquetaEstado[valor] : valor).join(", ")}.</p>
            ) : null}

            <label className="grid gap-1.5 text-sm font-medium text-[#334047]">
              Nuevo estado
              <select
                className="h-11 rounded-md border bg-white px-3 outline-none focus:border-[var(--mar)]"
                value={destino}
                onChange={(evento) => setDestino(evento.target.value as EstadoCarga)}
              >
                {opciones.map((opcion) => <option key={opcion.hacia} value={opcion.hacia}>{etiquetaEstado[opcion.hacia]}</option>)}
              </select>
            </label>

            <AreaTexto
              etiqueta={seleccion?.requiereMotivo ? "Justificación" : "Nota (opcional)"}
              value={motivo}
              onChange={(evento) => setMotivo(evento.target.value)}
              error={intentoEnviar && seleccion?.requiereMotivo && !motivo.trim() ? "La justificación es obligatoria para esta transición." : undefined}
              maxLength={2000}
            />
            <Campo etiqueta="Ubicación (opcional)" value={ubicacion} onChange={(evento) => setUbicacion(evento.target.value)} maxLength={180} />
            <Campo etiqueta="Fecha del evento (opcional)" type="datetime-local" value={fecha} onChange={(evento) => setFecha(evento.target.value)} />

            <div className="flex justify-end gap-3">
              <Boton type="button" variante="secundario" onClick={cerrar}>Cancelar</Boton>
              <Boton type="submit" cargando={mutacion.isPending} disabled={!destino}>
                Continuar
              </Boton>
            </div>
          </form>
        )}
      </Modal>
    </>
  );
}
