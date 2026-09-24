"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, exigirDatos } from "@/lib/api/client";

export function useCambiarContrasena() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async ({ actual, nueva }: { actual: string; nueva: string }) =>
      exigirDatos(await api.POST("/api/v1/me/password", { body: { actual, nueva } })),
    // Cambiar la contraseña levanta el bloqueo por contraseña temporal, así que
    // hay que releer la sesión para que la interfaz deje de redirigir.
    onSuccess: () => cliente.invalidateQueries(),
  });
}

export function useActualizarPerfil() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (datos: {
      first_name?: string;
      last_name?: string;
      phone?: string | null;
    }) => exigirDatos(await api.PATCH("/api/v1/me", { body: datos })),
    onSuccess: () => cliente.invalidateQueries(),
  });
}
