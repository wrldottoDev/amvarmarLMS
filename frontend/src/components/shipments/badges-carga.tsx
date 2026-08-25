import { AlertTriangle } from "lucide-react";
import type { EstadoCarga } from "@/lib/api/tipos";
import { clases } from "@/lib/utilidades";
import { esEstadoCarga, etiquetaEstado } from "@/features/shipments/catalogo-estados";

const estiloEstado: Record<EstadoCarga, string> = {
  PRE_ALERT: "bg-[#f1f3f4] text-[#536067]",
  IN_TRANSIT: "bg-[#e6f0fb] text-[#245f98]",
  RECEIVED: "bg-[#e8f0f2] text-[var(--mar)]",
  STORED: "bg-[#edf8f3] text-[var(--exito)]",
  DISPATCH_REQUESTED: "bg-[#fff4df] text-[#8a560d]",
  PREPARING: "bg-[#fff0e9] text-[var(--marca-oscura)]",
  DISPATCHED: "bg-[#eaeafb] text-[#514c9b]",
  DELIVERED: "bg-[#e8f6ec] text-[#226b3e]",
  CANCELLED: "bg-[#fff0ee] text-[var(--peligro)]",
};

export function BadgeEstado({ estado }: { estado: string }) {
  const estadoValido = esEstadoCarga(estado) ? estado : null;
  return (
    <span
      className={clases(
        "inline-flex min-h-7 items-center rounded px-2.5 py-1 text-xs font-bold whitespace-nowrap",
        estadoValido ? estiloEstado[estadoValido] : "bg-[#f1f3f4] text-[#536067]",
      )}
    >
      {estadoValido ? etiquetaEstado[estadoValido] : estado}
    </span>
  );
}

export function BadgePendientes({ cantidad, etiqueta = "pendiente" }: { cantidad: number; etiqueta?: string }) {
  if (cantidad <= 0) {
    return <span className="text-xs text-[var(--texto-secundario)]">Sin pendientes</span>;
  }

  return (
    <span className="inline-flex min-h-7 items-center gap-1.5 rounded border border-[#efc98c] bg-[#fff8e9] px-2.5 py-1 text-xs font-bold text-[#86520a] whitespace-nowrap">
      <AlertTriangle className="size-3.5" aria-hidden="true" />
      {cantidad} {cantidad === 1 ? etiqueta : `${etiqueta}s`}
    </span>
  );
}
