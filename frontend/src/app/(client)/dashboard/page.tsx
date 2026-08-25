"use client";

import { DashboardCargas } from "@/components/dashboard/dashboard-cargas";
import { QueHacer } from "@/components/dashboard/que-hacer";
import { useSesion } from "@/features/auth/contexto-sesion";

export default function PaginaDashboardCliente() {
  const { usuario } = useSesion();
  const nombre = usuario?.empresa?.trade_name ?? usuario?.empresa?.legal_name ?? "Mi empresa";

  return (
    <div className="space-y-5">
      {/* Lo pendiente va arriba del tablero: es la razón por la que alguien
          entra, y dejarlo debajo obliga a bajar para descubrir que hay algo
          que hacer. */}
      <QueHacer />
      <DashboardCargas vista="client" titulo={nombre} />
    </div>
  );
}
