import { Info } from "lucide-react";
import PaginaCargas from "@/app/(client)/shipments/page";

/**
 * Solo lo que todavía viaja: lo que llegó a destino, se entregó o se canceló
 * pasa al Historial (pedido de AMVARMAR, 2026-10-08).
 */
const ESTADOS_ACTIVOS = ["PRE_ALERT", "BOOKING_ASSIGNED", "IN_TRANSIT", "TRANSSHIPMENT"] as const;

export default function PaginaTransito() {
  return (
    <PaginaCargas
      tipoOrigen="TRANSIT"
      estadosVista={ESTADOS_ACTIVOS}
      encabezado={
        <p className="flex items-start gap-2 rounded-md border border-[var(--advertencia-borde)] bg-[var(--advertencia-tenue)] px-4 py-3 text-sm text-[var(--advertencia)]">
          <Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          Los tiempos estimados de arribo (ETA) pueden variar y están sujetos a cambios.
        </p>
      }
    />
  );
}
