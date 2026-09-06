"use client";

import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, Check, X } from "lucide-react";
import { useState } from "react";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { Campo } from "@/components/ui/campo";
import { api, exigirDatos } from "@/lib/api/client";
import type { PropuestaAccion } from "@/features/copilot/usar-chat";

type Estado = "pendiente" | "confirmada" | "rechazada";

function valorInicial(valor: PropuestaAccion["campos"][number]["valor"]) {
  return valor === null ? "" : String(valor);
}

/** Una propuesta de AMVI, editable, con confirmar/rechazar contra el
 * endpoint dedicado (ADR-0012) — nunca escribe nada por su cuenta. */
export function PropuestaCard({ propuesta }: { propuesta: PropuestaAccion }) {
  const [estado, setEstado] = useState<Estado>("pendiente");
  const [valores, setValores] = useState<Record<string, string>>(() =>
    Object.fromEntries(propuesta.campos.map((campo) => [campo.nombre, valorInicial(campo.valor)])),
  );

  const confirmar = useMutation({
    mutationFn: async () =>
      exigirDatos(
        await api.POST("/api/v1/copilot/proposals/{proposal_id}/confirm", {
          params: {
            path: { proposal_id: propuesta.id },
            header: { "Idempotency-Key": crypto.randomUUID() },
          },
          body: { campos: valores },
        }),
      ),
    onSuccess: () => setEstado("confirmada"),
  });

  const rechazar = useMutation({
    mutationFn: async () =>
      exigirDatos(
        await api.POST("/api/v1/copilot/proposals/{proposal_id}/reject", {
          params: { path: { proposal_id: propuesta.id } },
        }),
      ),
    onSuccess: () => setEstado("rechazada"),
  });

  const ocupado = confirmar.isPending || rechazar.isPending;

  return (
    <div className="max-w-[85%] space-y-3 rounded-lg border bg-[var(--fondo)] p-3 text-sm">
      <div>
        <p className="font-semibold text-[var(--texto)]">{propuesta.titulo}</p>
        <p className="text-xs text-[var(--texto-secundario)]">{propuesta.resumen_efecto}</p>
      </div>

      {propuesta.advertencias.length > 0 && estado === "pendiente" ? (
        <ul className="space-y-1">
          {propuesta.advertencias.map((advertencia) => (
            <li
              key={advertencia}
              className="flex gap-1.5 text-xs text-[var(--advertencia)]"
            >
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              {advertencia}
            </li>
          ))}
        </ul>
      ) : null}

      {estado === "pendiente" ? (
        <div className="grid gap-3">
          {propuesta.campos.map((campo) => (
            <Campo
              key={campo.nombre}
              etiqueta={campo.etiqueta}
              value={valores[campo.nombre] ?? ""}
              onChange={(evento) =>
                setValores((previos) => ({ ...previos, [campo.nombre]: evento.target.value }))
              }
              ayuda={campo.confianza < 1 ? "AMVI no está seguro de este valor — revisalo." : undefined}
              disabled={!campo.editable || ocupado}
            />
          ))}
        </div>
      ) : (
        <div className="grid gap-1">
          {propuesta.campos.map((campo) => (
            <p key={campo.nombre} className="text-xs text-[var(--texto-secundario)]">
              <span className="font-medium text-[var(--texto)]">{campo.etiqueta}:</span>{" "}
              {valores[campo.nombre] || "—"}
            </p>
          ))}
        </div>
      )}

      {confirmar.error ? <AvisoError error={confirmar.error} /> : null}
      {rechazar.error ? <AvisoError error={rechazar.error} /> : null}

      {estado === "confirmada" ? (
        <p className="text-xs font-medium text-[var(--exito)]">Confirmado.</p>
      ) : estado === "rechazada" ? (
        <p className="text-xs font-medium text-[var(--texto-secundario)]">Descartado.</p>
      ) : (
        <div className="flex justify-end gap-2">
          <Boton
            type="button"
            variante="secundario"
            cargando={rechazar.isPending}
            disabled={ocupado}
            onClick={() => rechazar.mutate()}
          >
            <X className="size-4" aria-hidden="true" />
            Rechazar
          </Boton>
          <Boton
            type="button"
            cargando={confirmar.isPending}
            disabled={ocupado}
            onClick={() => confirmar.mutate()}
          >
            <Check className="size-4" aria-hidden="true" />
            Confirmar
          </Boton>
        </div>
      )}
    </div>
  );
}
