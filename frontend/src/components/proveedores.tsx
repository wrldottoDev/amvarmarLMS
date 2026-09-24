"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { ProveedorSesion } from "@/features/auth/contexto-sesion";
import { GuardiaContrasena } from "@/features/auth/guardia-contrasena";
import { crearQueryClient } from "@/lib/query-client";

export function Proveedores({ children }: Readonly<{ children: React.ReactNode }>) {
  const [queryClient] = useState(crearQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <ProveedorSesion>
        {/* Escucha el bloqueo por contraseña temporal que emite el cliente HTTP. */}
        <GuardiaContrasena />
        {children}
      </ProveedorSesion>
    </QueryClientProvider>
  );
}
