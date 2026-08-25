"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, exigirDatos } from "@/lib/api/client";
import type { CrearDespacho, DetalleDespacho, PaginaDespachos } from "@/lib/api/tipos";
import type { EstadoDespacho } from "./catalogo";

export const claveDespachos = ["despachos"] as const;

export function useDespachos(opciones?: { estado?: EstadoDespacho; limite?: number }) {
  return useQuery({
    queryKey: [...claveDespachos, "lista", opciones?.estado ?? "todos"],
    queryFn: async (): Promise<PaginaDespachos> =>
      exigirDatos(
        await api.GET("/api/v1/dispatch-requests", {
          params: {
            query: {
              // El filtro del backend acepta varios estados; la interfaz usa
              // uno o ninguno, que es lo que la gente realmente necesita.
              ...(opciones?.estado ? { status: [opciones.estado] } : {}),
              limit: opciones?.limite ?? 25,
            },
          },
        }),
      ),
  });
}

export function useDespacho(id: string) {
  return useQuery({
    queryKey: [...claveDespachos, "detalle", id],
    queryFn: async (): Promise<DetalleDespacho> =>
      exigirDatos(
        await api.GET("/api/v1/dispatch-requests/{dispatch_id}", {
          params: { path: { dispatch_id: id } },
        }),
      ),
    enabled: Boolean(id),
  });
}

function invalidarTodo(cliente: ReturnType<typeof useQueryClient>) {
  cliente.invalidateQueries({ queryKey: claveDespachos });
  // Las cargas cambian de estado al entrar o salir de un despacho. Sin esto el
  // listado las seguiría mostrando como disponibles.
  cliente.invalidateQueries({ queryKey: ["cargas"] });
  cliente.invalidateQueries({ queryKey: ["dashboard"] });
}

export function useCrearDespacho(companyId: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (datos: CrearDespacho) =>
      exigirDatos(
        await api.POST("/api/v1/dispatch-requests", {
          params: {
            query: { company_id: companyId },
            // Si el botón se toca dos veces o la red reintenta, la clave hace
            // que el servidor devuelva la misma solicitud en vez de crear dos.
            header: { "Idempotency-Key": crypto.randomUUID() },
          },
          body: datos,
        }),
      ),
    onSuccess: () => invalidarTodo(cliente),
  });
}

export function useAprobar(id: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (notas?: string) =>
      exigirDatos(
        await api.POST("/api/v1/dispatch-requests/{dispatch_id}/approve", {
          params: { path: { dispatch_id: id } },
          body: { notes: notas ?? null },
        }),
      ),
    onSuccess: () => invalidarTodo(cliente),
  });
}

export function useRechazar(id: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (motivo: string) =>
      exigirDatos(
        await api.POST("/api/v1/dispatch-requests/{dispatch_id}/reject", {
          params: { path: { dispatch_id: id } },
          body: { reason: motivo },
        }),
      ),
    onSuccess: () => invalidarTodo(cliente),
  });
}

export function usePreparar(id: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      exigirDatos(
        await api.POST("/api/v1/dispatch-requests/{dispatch_id}/prepare", {
          params: { path: { dispatch_id: id } },
          body: {},
        }),
      ),
    onSuccess: () => invalidarTodo(cliente),
  });
}

export function useCompletar(id: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      exigirDatos(
        await api.POST("/api/v1/dispatch-requests/{dispatch_id}/complete", {
          params: { path: { dispatch_id: id } },
          body: {},
        }),
      ),
    onSuccess: () => invalidarTodo(cliente),
  });
}

export function useCancelar(id: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (notas?: string) =>
      exigirDatos(
        await api.POST("/api/v1/dispatch-requests/{dispatch_id}/cancel", {
          params: { path: { dispatch_id: id } },
          body: { notes: notas ?? null },
        }),
      ),
    onSuccess: () => invalidarTodo(cliente),
  });
}
