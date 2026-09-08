"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, exigirDatos } from "@/lib/api/client";
import type { PaginaNotificaciones } from "@/lib/api/tipos";

export const claveNotificaciones = ["notificaciones"] as const;

export function useNotificaciones(opciones?: { soloNoLeidas?: boolean; limite?: number }) {
  return useQuery({
    queryKey: [...claveNotificaciones, opciones?.soloNoLeidas ?? false, opciones?.limite ?? 25],
    queryFn: async (): Promise<PaginaNotificaciones> =>
      exigirDatos(
        await api.GET("/api/v1/notifications", {
          params: {
            query: {
              unread: opciones?.soloNoLeidas ?? false,
              limit: opciones?.limite ?? 25,
            },
          },
        }),
      ),
    // La campana tiene que enterarse sin que nadie recargue. Un minuto alcanza
    // para un aviso de logística y no castiga al servidor.
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
}

export function useMarcarLeida() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      exigirDatos(
        await api.POST("/api/v1/notifications/{notification_id}/read", {
          params: { path: { notification_id: id } },
        }),
      ),
    onSuccess: () => cliente.invalidateQueries({ queryKey: claveNotificaciones }),
  });
}

export function useMarcarTodasLeidas() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async () => exigirDatos(await api.POST("/api/v1/notifications/read-all", {})),
    onSuccess: () => cliente.invalidateQueries({ queryKey: claveNotificaciones }),
  });
}
