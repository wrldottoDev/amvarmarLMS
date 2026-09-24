"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, exigirDatos } from "@/lib/api/client";
import { refrescarAccessToken } from "@/lib/api/refresh-mutex";
import { guardarAccessToken, limpiarAccessToken, obtenerAccessToken, suscribirToken } from "@/lib/api/token-store";
import type { UsuarioActual } from "@/lib/api/tipos";

type EstadoSesion = "cargando" | "autenticada" | "anonima";

interface ContextoSesionValor {
  usuario: UsuarioActual | null;
  estado: EstadoSesion;
  iniciarSesion: (email: string, password: string) => Promise<UsuarioActual>;
  cerrarSesion: () => Promise<void>;
  cerrarTodasLasSesiones: () => Promise<void>;
  tienePermiso: (permiso: string) => boolean;
}

const ContextoSesion = createContext<ContextoSesionValor | null>(null);

function nombreDispositivo() {
  if (typeof navigator === "undefined") return "Navegador web";
  if (/iPhone|iPad/.test(navigator.userAgent)) return "Safari en iOS";
  if (/Android/.test(navigator.userAgent)) return "Navegador en Android";
  if (/Mac/.test(navigator.userAgent)) return "Navegador en macOS";
  if (/Windows/.test(navigator.userAgent)) return "Navegador en Windows";
  return "Navegador web";
}

export function ProveedorSesion({ children }: Readonly<{ children: React.ReactNode }>) {
  const [usuario, setUsuario] = useState<UsuarioActual | null>(null);
  const [estado, setEstado] = useState<EstadoSesion>("cargando");
  const queryClient = useQueryClient();
  // Ref y no estado: la suscripción entre pestañas se registra una sola vez y
  // necesita leer el usuario actual sin volver a suscribirse en cada cambio.
  const usuarioRef = useRef<UsuarioActual | null>(null);

  const aplicarUsuario = useCallback((nuevoUsuario: UsuarioActual | null) => {
    usuarioRef.current = nuevoUsuario;
    setUsuario(nuevoUsuario);
    setEstado(nuevoUsuario ? "autenticada" : "anonima");
  }, []);

  const cargarUsuario = useCallback(async () => {
    const resultado = await api.GET("/api/v1/me");
    const datos = exigirDatos(resultado);
    aplicarUsuario(datos);
    return datos;
  }, [aplicarUsuario]);

  useEffect(() => {
    let activo = true;

    async function recuperarSesion() {
      const token = obtenerAccessToken() ?? (await refrescarAccessToken());
      if (!activo) return;
      if (!token) {
        aplicarUsuario(null);
        return;
      }

      try {
        await cargarUsuario();
      } catch {
        if (activo) aplicarUsuario(null);
      }
    }

    const cancelarSuscripcion = suscribirToken((token) => {
      if (!activo) return;

      if (!token) {
        queryClient.clear();
        aplicarUsuario(null);
        return;
      }

      // Otra pestaña inició sesión. Sin esto, esta pestaña se quedaría en la
      // pantalla de login pese a haber una sesión válida en el navegador.
      if (!usuarioRef.current) {
        void cargarUsuario().catch(() => {
          if (activo) aplicarUsuario(null);
        });
      }
    });

    void recuperarSesion();
    return () => {
      activo = false;
      cancelarSuscripcion();
    };
  }, [aplicarUsuario, cargarUsuario, queryClient]);

  const iniciarSesion = useCallback(
    async (email: string, password: string) => {
      const resultado = await api.POST("/api/v1/auth/login", {
        body: {
          email,
          password,
          client_type: "WEB",
          device_name: nombreDispositivo(),
        },
      });
      const tokens = exigirDatos(resultado);
      guardarAccessToken(tokens.access_token);
      return cargarUsuario();
    },
    [cargarUsuario],
  );

  const cerrarSesion = useCallback(async () => {
    try {
      const resultado = await api.POST("/api/v1/auth/logout");
      exigirDatos(resultado);
    } finally {
      limpiarAccessToken();
      queryClient.clear();
      aplicarUsuario(null);
    }
  }, [aplicarUsuario, queryClient]);

  const cerrarTodasLasSesiones = useCallback(async () => {
    try {
      const resultado = await api.POST("/api/v1/auth/logout-all");
      exigirDatos(resultado);
    } finally {
      limpiarAccessToken();
      queryClient.clear();
      aplicarUsuario(null);
    }
  }, [aplicarUsuario, queryClient]);

  const tienePermiso = useCallback(
    (permiso: string) => Boolean(usuario?.permisos.includes(permiso)),
    [usuario],
  );

  return (
    <ContextoSesion.Provider
      value={{ usuario, estado, iniciarSesion, cerrarSesion, cerrarTodasLasSesiones, tienePermiso }}
    >
      {children}
    </ContextoSesion.Provider>
  );
}

export function useSesion() {
  const contexto = useContext(ContextoSesion);
  if (!contexto) throw new Error("useSesion debe usarse dentro de ProveedorSesion.");
  return contexto;
}
