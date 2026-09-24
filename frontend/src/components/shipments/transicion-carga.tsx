"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Check, RefreshCw } from "lucide-react";
import { type FormEvent, useState } from "react";
import { BadgeEstado } from "./badges-carga";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { AreaTexto, Campo } from "@/components/ui/campo";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import { Modal } from "@/components/ui/modal";
import { useSesion } from "@/features/auth/contexto-sesion";
import { esEstadoCarga, etiquetaEstado } from "@/features/shipments/catalogo-estados";
import { api, ErrorApi, exigirDatos } from "@/lib/api/client";
import type { EstadoCarga } from "@/lib/api/tipos";

const PERMISOS_DE_ESTADO = new Set([
  "shipments.transition.forward",
  "shipments.transition.backward",
  "shipments.transition.revert_delivered",
  "shipments.cancel.prealert",
  "shipments.cancel.in_transit",
  "shipments.reopen",
]);

function permitidasDesdeDetalles(error: ErrorApi) {
  if (error.code !== "SHIPMENT_TRANSITION_INVALID" || !Array.isArray(error.details)) return [];
  const primerDetalle = error.details[0];
  if (!primerDetalle || typeof primerDetalle !== "object" || !("allowed" in primerDetalle)) {
    return [];
  }
  const allowed = primerDetalle.allowed;
  return Array.isArray(allowed)
    ? allowed.filter((valor): valor is string => typeof valor === "string")
    : [];
}

export function TransicionCarga({
  cargaId,
  estado,
  rowVersion,
  modo = "boton",
}: {
  cargaId: string;
  estado: string;
  rowVersion: number;
  modo?: "boton" | "badge";
}) {
  const [abierto, setAbierto] = useState(false);
  const [destino, setDestino] = useState<EstadoCarga | "">("");
  const [motivo, setMotivo] = useState("");
  const [ubicacion, setUbicacion] = useState("");
  const [fecha, setFecha] = useState("");
  const [confirmarDespacho, setConfirmarDespacho] = useState(false);
  const [intentoEnviar, setIntentoEnviar] = useState(false);
  const { usuario } = useSesion();
  const queryClient = useQueryClient();

  const puedeCambiar = usuario?.permisos.some((permiso) => PERMISOS_DE_ESTADO.has(permiso));
  const disponibles = useQuery({
    queryKey: ["transiciones-disponibles", cargaId, estado, rowVersion],
    queryFn: async () =>
      exigirDatos(
        await api.GET("/api/v1/shipments/{shipment_id}/transitions/available", {
          params: { path: { shipment_id: cargaId } },
        }),
      ),
    enabled: abierto && Boolean(puedeCambiar),
  });

  const primeraDisponible = disponibles.data?.find((opcion) => !opcion.blocked)?.to_status ?? "";
  const destinoEfectivo = destino || (esEstadoCarga(primeraDisponible) ? primeraDisponible : "");
  const seleccion = disponibles.data?.find((opcion) => opcion.to_status === destinoEfectivo);

  const mutacion = useMutation({
    mutationFn: async () => {
      if (!destinoEfectivo || !esEstadoCarga(destinoEfectivo)) {
        throw new Error("Seleccioná el nuevo estado.");
      }
      return exigirDatos(
        await api.POST("/api/v1/shipments/{shipment_id}/transitions", {
          params: { path: { shipment_id: cargaId } },
          body: {
            to_status: destinoEfectivo,
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
        queryClient.invalidateQueries({ queryKey: ["transiciones-disponibles", cargaId] }),
      ]);
      cerrar();
    },
    onError: async (error) => {
      if (error instanceof ErrorApi && error.code === "SHIPMENT_VERSION_CONFLICT") {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["carga", cargaId] }),
          queryClient.invalidateQueries({ queryKey: ["cargas"] }),
        ]);
      }
    },
  });

  function cerrar() {
    setAbierto(false);
    setDestino("");
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
    if (!seleccion || seleccion.blocked) return;
    if (seleccion.requires_reason && !motivo.trim()) return;
    if (estado === "PREPARING" && destinoEfectivo === "DISPATCHED") {
      setConfirmarDespacho(true);
      return;
    }
    mutacion.mutate();
  }

  if (!puedeCambiar) return modo === "badge" ? <BadgeEstado estado={estado} /> : null;

  const permitidas = mutacion.error instanceof ErrorApi ? permitidasDesdeDetalles(mutacion.error) : [];
  const conflictoVersion =
    mutacion.error instanceof ErrorApi && mutacion.error.code === "SHIPMENT_VERSION_CONFLICT";

  return (
    <>
      {modo === "badge" ? (
        <button
          type="button"
          onClick={() => setAbierto(true)}
          className="rounded-full outline-none ring-offset-2 focus-visible:ring-2 focus-visible:ring-[var(--mar)]"
          aria-label={`Cambiar estado: ${estado}`}
          title="Cambiar estado"
        >
          <BadgeEstado estado={estado} />
        </button>
      ) : (
        <Boton onClick={() => setAbierto(true)}>
          <RefreshCw className="size-4" aria-hidden="true" />
          Cambiar estado
        </Boton>
      )}

      <Modal
        abierto={abierto}
        cerrar={cerrar}
        titulo={confirmarDespacho ? "Confirmar despacho" : "Cambiar estado"}
      >
        {confirmarDespacho ? (
          <div className="space-y-5 p-5">
            <p className="text-sm font-medium">¿Confirmás la salida de esta carga?</p>
            <div className="flex items-center gap-3 border-y bg-[var(--hover)] p-4">
              <BadgeEstado estado={estado} />
              <ArrowRight className="size-4 text-[var(--texto-secundario)]" />
              <BadgeEstado estado="DISPATCHED" />
            </div>
            {mutacion.error ? <AvisoError error={mutacion.error} /> : null}
            <div className="flex justify-end gap-3">
              <Boton variante="secundario" onClick={() => setConfirmarDespacho(false)}>
                Volver
              </Boton>
              <Boton cargando={mutacion.isPending} onClick={() => mutacion.mutate()}>
                <Check className="size-4" aria-hidden="true" />
                Confirmar salida
              </Boton>
            </div>
          </div>
        ) : disponibles.isPending ? (
          <CargandoPagina texto="Consultando transiciones" />
        ) : (
          <form className="grid gap-5 p-5" onSubmit={enviar}>
            {disponibles.error ? <AvisoError error={disponibles.error} /> : null}
            {mutacion.error ? <AvisoError error={mutacion.error} /> : null}
            {conflictoVersion ? (
              <p className="bg-[var(--advertencia-tenue)] px-4 py-3 text-sm text-[var(--advertencia)]">
                Otra persona modificó esta carga. La fila ya se está actualizando.
              </p>
            ) : null}
            {permitidas.length ? (
              <p className="text-xs text-[var(--texto-secundario)]">
                El servidor permite: {permitidas.map((valor) => (esEstadoCarga(valor) ? etiquetaEstado[valor] : valor)).join(", ")}.
              </p>
            ) : null}

            {disponibles.data?.length ? (
              <label className="grid gap-1.5 text-sm font-medium text-[var(--texto)]">
                Nuevo estado
                <select
                  className="h-11 rounded-md border bg-[var(--superficie)] px-3 outline-none focus:border-[var(--mar)]"
                  value={destinoEfectivo}
                  onChange={(evento) => setDestino(evento.target.value as EstadoCarga)}
                >
                  {disponibles.data.map((opcion) => (
                    <option key={opcion.to_status} value={opcion.to_status} disabled={opcion.blocked}>
                      {opcion.label}{opcion.blocked ? " — bloqueada" : ""}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <p className="text-sm text-[var(--texto-secundario)]">
                No hay transiciones disponibles desde este estado.
              </p>
            )}

            {seleccion?.blockers.map((bloqueo) => (
              <p
                key={bloqueo.code}
                className="border-l-2 border-[var(--advertencia)] pl-3 text-sm text-[var(--advertencia)]"
              >
                {bloqueo.message}
              </p>
            ))}

            <AreaTexto
              etiqueta={seleccion?.requires_reason ? "Justificación" : "Nota (opcional)"}
              value={motivo}
              onChange={(evento) => setMotivo(evento.target.value)}
              error={
                intentoEnviar && seleccion?.requires_reason && !motivo.trim()
                  ? "La justificación es obligatoria para esta transición."
                  : undefined
              }
              maxLength={2000}
            />
            <Campo
              etiqueta="Ubicación (opcional)"
              value={ubicacion}
              onChange={(evento) => setUbicacion(evento.target.value)}
              maxLength={180}
            />
            <Campo
              etiqueta="Fecha del evento (opcional)"
              type="datetime-local"
              value={fecha}
              onChange={(evento) => setFecha(evento.target.value)}
            />

            <div className="flex justify-end gap-3">
              <Boton type="button" variante="secundario" onClick={cerrar}>
                Cancelar
              </Boton>
              <Boton
                type="submit"
                cargando={mutacion.isPending}
                disabled={!destinoEfectivo || seleccion?.blocked}
              >
                Continuar
              </Boton>
            </div>
          </form>
        )}
      </Modal>
    </>
  );
}
