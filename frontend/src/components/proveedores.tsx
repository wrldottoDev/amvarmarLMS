"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { ProveedorSesion } from "@/features/auth/contexto-sesion";
import { crearQueryClient } from "@/lib/query-client";

export function Proveedores({ children }: Readonly<{ children: React.ReactNode }>) {
  const [queryClient] = useState(crearQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <ProveedorSesion>{children}</ProveedorSesion>
    </QueryClientProvider>
  );
}
