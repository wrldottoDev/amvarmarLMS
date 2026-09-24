"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { DashboardCargas } from "@/components/dashboard/dashboard-cargas";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import { useSesion } from "@/features/auth/contexto-sesion";

export default function PaginaOperaciones() {
  const { usuario } = useSesion();
  const router = useRouter();

  useEffect(() => {
    if (usuario?.empresa) router.replace("/dashboard");
  }, [router, usuario?.empresa]);

  if (usuario?.empresa) return <CargandoPagina texto="Abriendo dashboard" />;
  return <DashboardCargas vista="operations" titulo="Operaciones AMVARMAR" />;
}
