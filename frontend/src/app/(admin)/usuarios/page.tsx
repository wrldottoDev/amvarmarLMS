"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { GestionUsuarios } from "@/components/admin/gestion-usuarios";
import { CargandoPagina, EstadoVacio } from "@/components/ui/estados-pagina";
import { useSesion } from "@/features/auth/contexto-sesion";

/**
 * Solo para AMVARMAR (ADR-0017).
 *
 * Antes esta ruta servía a dos públicos y decidía el alcance según quién
 * miraba: un cliente veía a la gente de su propia empresa. Ya no — a los
 * usuarios de una empresa los da de alta AMVARMAR, así que el cliente perdió
 * `users.manage` y esta pantalla dejó de tener un modo "mi empresa".
 *
 * El backend responde 403 igual; esto solo evita que quien llegó por la URL
 * se encuentre una pantalla que falla en vez de una explicación.
 */
export default function PaginaUsuarios() {
  return (
    <Suspense fallback={<CargandoPagina />}>
      <Contenido />
    </Suspense>
  );
}

function Contenido() {
  const parametros = useSearchParams();
  const empresaFiltro = parametros.get("empresa") ?? undefined;
  const { usuario } = useSesion();

  if (!usuario) return <CargandoPagina />;

  if (usuario.empresa) {
    return (
      <EstadoVacio
        titulo="Esta sección es de AMVARMAR"
        descripcion="Las cuentas de tu empresa las administra AMVARMAR. Escribinos si necesitás dar de alta o quitar a alguien."
      />
    );
  }

  return <GestionUsuarios modo={{ alcance: "completo", empresaFiltro }} />;
}
