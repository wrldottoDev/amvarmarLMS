"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import { useSesion } from "./contexto-sesion";

export function PortalProtegido({ children }: Readonly<{ children: React.ReactNode }>) {
  const { estado } = useSesion();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (estado === "anonima") {
      router.replace(`/login?regreso=${encodeURIComponent(pathname)}`);
    }
  }, [estado, pathname, router]);

  if (estado !== "autenticada") {
    return <CargandoPagina texto="Verificando sesión" />;
  }

  return children;
}
