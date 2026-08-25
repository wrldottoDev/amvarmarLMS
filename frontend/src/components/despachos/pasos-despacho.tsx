import { Check } from "lucide-react";
import { clases } from "@/lib/utilidades";

/**
 * Línea de pasos del despacho.
 *
 * Existe para responder sin leer nada: ¿en qué punto va y cuánto falta? Un
 * estado suelto no dice si viene antes o después de otro; una línea sí.
 */
const PASOS = [
  { estado: "PENDING", etiqueta: "Solicitado" },
  { estado: "APPROVED", etiqueta: "Aprobado" },
  { estado: "PREPARING", etiqueta: "Preparando" },
  { estado: "COMPLETED", etiqueta: "Despachado" },
] as const;

const TERMINADOS = new Set(["REJECTED", "CANCELLED"]);

export function PasosDespacho({ estado }: { estado: string }) {
  // Rechazado o cancelado no es un punto de la línea: es una salida. Mostrar la
  // línea igual sugeriría que todavía avanza.
  if (TERMINADOS.has(estado)) return null;

  const actual = PASOS.findIndex((paso) => paso.estado === estado);

  return (
    <ol className="flex items-center gap-1" aria-label="Avance del despacho">
      {PASOS.map((paso, indice) => {
        const completado = indice < actual;
        const activo = indice === actual;
        return (
          <li key={paso.estado} className="flex flex-1 items-center gap-1">
            <div className="flex flex-1 flex-col items-center gap-1.5">
              <span
                className={clases(
                  "grid size-7 place-items-center rounded-full border-2 text-xs font-bold",
                  completado && "border-[var(--mar)] bg-[var(--mar)] text-white",
                  activo && "border-[var(--mar)] bg-white text-[var(--mar)]",
                  !completado && !activo && "border-[#d6dfe2] bg-white text-[#9aa9ae]",
                )}
                aria-current={activo ? "step" : undefined}
              >
                {completado ? <Check className="size-3.5" aria-hidden="true" /> : indice + 1}
              </span>
              <span
                className={clases(
                  "text-center text-[11px] leading-tight",
                  activo ? "font-semibold text-[var(--texto)]" : "text-[var(--texto-secundario)]",
                )}
              >
                {paso.etiqueta}
              </span>
            </div>
            {indice < PASOS.length - 1 ? (
              <span
                className={clases(
                  "mb-5 h-0.5 w-full flex-1",
                  indice < actual ? "bg-[var(--mar)]" : "bg-[#d6dfe2]",
                )}
                aria-hidden="true"
              />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
