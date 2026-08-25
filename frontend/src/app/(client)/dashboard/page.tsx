"use client";

import { DashboardCargas } from "@/components/dashboard/dashboard-cargas";
import { useSesion } from "@/features/auth/contexto-sesion";

export default function PaginaDashboardCliente() {
  const { usuario } = useSesion();
  const nombre = usuario?.empresa?.trade_name ?? usuario?.empresa?.legal_name ?? "Mi empresa";
  return <DashboardCargas vista="client" titulo={nombre} />;
}
