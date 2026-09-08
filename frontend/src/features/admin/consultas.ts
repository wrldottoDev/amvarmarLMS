"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, exigirDatos } from "@/lib/api/client";
import type {
  CrearCarga,
  CrearEmpresa,
  CrearUsuario,
  EmpresaAdmin,
  UsuarioAdmin,
} from "@/lib/api/tipos";

export const claveEmpresas = ["admin", "empresas"] as const;
export const claveUsuarios = ["admin", "usuarios"] as const;

// Para selectores y filtros que necesitan "todas las empresas" en una sola
// lista, no una página: el tope duro del servidor (ver `LIMITE_MAXIMO` en
// `app/core/pagination.py`), no una paginación real. `useEmpresasPaginadas`
// es la versión con "Cargar más" para el listado de /empresas.
export function useEmpresas(incluirInactivas = false, habilitada = true) {
  return useQuery({
    queryKey: [...claveEmpresas, incluirInactivas, "simple"],
    queryFn: async (): Promise<EmpresaAdmin[]> => {
      const pagina = exigirDatos(
        await api.GET("/api/v1/admin/companies", {
          params: { query: { incluir_inactivas: incluirInactivas, limit: 100 } },
        }),
      );
      return pagina.items;
    },
    enabled: habilitada,
  });
}

export function useEmpresasPaginadas(incluirInactivas = false) {
  return useInfiniteQuery({
    queryKey: [...claveEmpresas, incluirInactivas, "paginado"],
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) =>
      exigirDatos(
        await api.GET("/api/v1/admin/companies", {
          params: { query: { incluir_inactivas: incluirInactivas, cursor: pageParam } },
        }),
      ),
    getNextPageParam: (ultimaPagina) =>
      ultimaPagina.has_more && ultimaPagina.next_cursor ? ultimaPagina.next_cursor : undefined,
  });
}

export function useCrearEmpresa() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (datos: CrearEmpresa) =>
      exigirDatos(await api.POST("/api/v1/admin/companies", { body: datos })),
    onSuccess: () => cliente.invalidateQueries({ queryKey: claveEmpresas }),
  });
}

export function useActualizarEmpresa() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...cambios }: { id: string } & Record<string, unknown>) =>
      exigirDatos(
        await api.PATCH("/api/v1/admin/companies/{company_id}", {
          params: { path: { company_id: id } },
          body: cambios as never,
        }),
      ),
    onSuccess: () => cliente.invalidateQueries({ queryKey: claveEmpresas }),
  });
}

export function useDesactivarEmpresa() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      exigirDatos(
        await api.DELETE("/api/v1/admin/companies/{company_id}", {
          params: { path: { company_id: id } },
        }),
      ),
    onSuccess: () => {
      cliente.invalidateQueries({ queryKey: claveEmpresas });
      // Desactivar la empresa suspende a sus usuarios: el listado de usuarios
      // queda desactualizado si no se refresca.
      cliente.invalidateQueries({ queryKey: claveUsuarios });
    },
  });
}

export function useUsuarios(companyId?: string) {
  return useQuery({
    queryKey: [...claveUsuarios, companyId ?? "todos"],
    queryFn: async (): Promise<UsuarioAdmin[]> =>
      exigirDatos(
        await api.GET("/api/v1/admin/users", {
          params: { query: companyId ? { company_id: companyId } : {} },
        }),
      ),
  });
}

export function useCrearUsuario() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (datos: CrearUsuario) =>
      exigirDatos(await api.POST("/api/v1/admin/users", { body: datos })),
    onSuccess: () => {
      cliente.invalidateQueries({ queryKey: claveUsuarios });
      cliente.invalidateQueries({ queryKey: claveEmpresas });
    },
  });
}

export function useActualizarUsuario() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...cambios }: { id: string } & Record<string, unknown>) =>
      exigirDatos(
        await api.PATCH("/api/v1/admin/users/{user_id}", {
          params: { path: { user_id: id } },
          body: cambios as never,
        }),
      ),
    onSuccess: () => cliente.invalidateQueries({ queryKey: claveUsuarios }),
  });
}

export function useRestablecerContrasena() {
  return useMutation({
    mutationFn: async (id: string) =>
      exigirDatos(
        await api.POST("/api/v1/admin/users/{user_id}/reset-password", {
          params: { path: { user_id: id } },
        }),
      ),
  });
}

export function useDesactivarUsuario() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      exigirDatos(
        await api.DELETE("/api/v1/admin/users/{user_id}", {
          params: { path: { user_id: id } },
        }),
      ),
    onSuccess: () => cliente.invalidateQueries({ queryKey: claveUsuarios }),
  });
}

export function useUbicaciones() {
  return useQuery({
    queryKey: ["catalogos", "ubicaciones"],
    queryFn: async () =>
      exigirDatos(await api.GET("/api/v1/shipments/catalogos/locations", {})),
    // Puertos y ciudades: cambian una vez al año, no hace falta refrescarlos.
    staleTime: 30 * 60 * 1000,
  });
}

export function useBodegas() {
  return useQuery({
    queryKey: ["catalogos", "bodegas"],
    queryFn: async () =>
      exigirDatos(await api.GET("/api/v1/shipments/catalogos/facilities", {})),
    staleTime: 30 * 60 * 1000,
  });
}

export function useCrearCarga() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: async (datos: CrearCarga) =>
      exigirDatos(await api.POST("/api/v1/shipments", { body: datos })),
    onSuccess: () => {
      cliente.invalidateQueries({ queryKey: ["cargas"] });
      cliente.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}
