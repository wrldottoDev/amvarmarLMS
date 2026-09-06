"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ErrorApi, exigirDatos } from "@/lib/api/client";
import type { CrearDespacho, DetalleDespacho } from "@/lib/api/tipos";
import type { EstadoDespacho } from "./catalogo";

export const claveDespachos = ["despachos"] as const;

export function useDespachos(opciones?: { estado?: EstadoDespacho; limite?: number }) {
  return useInfiniteQuery({
    queryKey: [...claveDespachos, "lista", opciones?.estado ?? "todos"],
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) =>
      exigirDatos(
        await api.GET("/api/v1/dispatch-requests", {
          params: {
            query: {
              // El filtro del backend acepta varios estados; la interfaz usa
              // uno o ninguno, que es lo que la gente realmente necesita.
              ...(opciones?.estado ? { status: [opciones.estado] } : {}),
              limit: opciones?.limite ?? 25,
              cursor: pageParam,
            },
          },
        }),
      ),
    getNextPageParam: (ultimaPagina) =>
      ultimaPagina.has_more && ultimaPagina.next_cursor ? ultimaPagina.next_cursor : undefined,
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

// Otra persona ya cambió el despacho: el `row_version` que se mandó quedó
// viejo. Se refresca el detalle para que la próxima acción use el actual, en
// vez de reintentar a ciegas con el mismo valor.
function alConflictoDeVersion(error: unknown, cliente: ReturnType<typeof useQueryClient>) {
  if (error instanceof ErrorApi && error.code === "DISPATCH_VERSION_CONFLICT") {
    invalidarTodo(cliente);
  }
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
    mutationFn: async ({ notas, rowVersion }: { notas?: string; rowVersion: number }) =>
      exigirDatos(
        await api.POST("/api/v1/dispatch-requests/{dispatch_id}/approve", {
          params: { path: { dispatch_id: id } },
          body: { notes: notas ?? null, row_version: rowVersion },
        }),
      ),
    onSuccess: () => invalidarTodo(cliente),
    onError: (error) => alConflictoDeVersion(error, cliente),
  });
}

export function useRechazar(id: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async ({ motivo, rowVersion }: { motivo: string; rowVersion: number }) =>
      exigirDatos(
        await api.POST("/api/v1/dispatch-requests/{dispatch_id}/reject", {
          params: { path: { dispatch_id: id } },
          body: { reason: motivo, row_version: rowVersion },
        }),
      ),
    onSuccess: () => invalidarTodo(cliente),
    onError: (error) => alConflictoDeVersion(error, cliente),
  });
}

export function usePreparar(id: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (rowVersion: number) =>
      exigirDatos(
        await api.POST("/api/v1/dispatch-requests/{dispatch_id}/prepare", {
          params: { path: { dispatch_id: id } },
          body: { row_version: rowVersion },
        }),
      ),
    onSuccess: () => invalidarTodo(cliente),
    onError: (error) => alConflictoDeVersion(error, cliente),
  });
}

export function useCompletar(id: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (rowVersion: number) =>
      exigirDatos(
        await api.POST("/api/v1/dispatch-requests/{dispatch_id}/complete", {
          params: { path: { dispatch_id: id } },
          body: { row_version: rowVersion },
        }),
      ),
    onSuccess: () => invalidarTodo(cliente),
    onError: (error) => alConflictoDeVersion(error, cliente),
  });
}

export function useDespachar(id: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (rowVersion: number) =>
      exigirDatos(
        await api.POST("/api/v1/dispatch-requests/{dispatch_id}/dispatch", {
          params: { path: { dispatch_id: id } },
          body: { row_version: rowVersion },
        }),
      ),
    onSuccess: () => invalidarTodo(cliente),
    onError: (error) => alConflictoDeVersion(error, cliente),
  });
}

export function useCancelar(id: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async ({ motivo, rowVersion }: { motivo: string; rowVersion: number }) =>
      exigirDatos(
        await api.POST("/api/v1/dispatch-requests/{dispatch_id}/cancel", {
          params: { path: { dispatch_id: id } },
          body: { notes: motivo, row_version: rowVersion },
        }),
      ),
    onSuccess: () => invalidarTodo(cliente),
    onError: (error) => alConflictoDeVersion(error, cliente),
  });
}
