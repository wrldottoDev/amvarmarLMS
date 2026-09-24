import { AlertTriangle } from "lucide-react";
import type { EstadoCarga } from "@/lib/api/tipos";
import { clases } from "@/lib/utilidades";
import { esEstadoCarga, etiquetaEstado } from "@/features/shipments/catalogo-estados";

const estiloEstado: Record<EstadoCarga, string> = {
  PRE_ALERT: "bg-[var(--hover)] text-[var(--texto-secundario)]",
  IN_TRANSIT: "bg-[var(--marca-tenue)] text-[var(--marca-oscura)]",
  RECEIVED: "bg-[var(--marca-tenue)] text-[var(--mar)]",
  STORED: "bg-[var(--exito-tenue)] text-[var(--exito)]",
  DISPATCH_REQUESTED: "bg-[var(--advertencia-tenue)] text-[var(--advertencia)]",
  PREPARING: "bg-[var(--marca-tenue)] text-[var(--marca-oscura)]",
  DISPATCHED: "bg-[var(--marca-tenue)] text-[var(--marca-oscura)]",
  DELIVERED: "bg-[var(--exito-tenue)] text-[var(--exito)]",
  CANCELLED: "bg-[var(--peligro-tenue)] text-[var(--peligro)]",
};

export function BadgeEstado({ estado }: { estado: string }) {
  const estadoValido = esEstadoCarga(estado) ? estado : null;
  return (
    <span
      className={clases(
        "inline-flex min-h-7 items-center rounded px-2.5 py-1 text-xs font-bold whitespace-nowrap",
        estadoValido ? estiloEstado[estadoValido] : "bg-[var(--hover)] text-[var(--texto-secundario)]",
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
    <span className="inline-flex min-h-7 items-center gap-1.5 rounded border border-[var(--advertencia-borde)] bg-[var(--advertencia-tenue)] px-2.5 py-1 text-xs font-bold text-[var(--advertencia)] whitespace-nowrap">
      <AlertTriangle className="size-3.5" aria-hidden="true" />
      {cantidad} {cantidad === 1 ? etiqueta : `${etiqueta}s`}
    </span>
  );
}
