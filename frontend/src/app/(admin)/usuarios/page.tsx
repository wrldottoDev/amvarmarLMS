"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { GestionUsuarios } from "@/components/admin/gestion-usuarios";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import { useSesion } from "@/features/auth/contexto-sesion";

/**
 * Una sola ruta para los dos públicos: `(admin)` y `(client)` no cambian la
 * URL (son grupos de Next.js), así que no puede haber una segunda página en
 * `/usuarios` para clientes — esta misma decide el alcance según quién mira.
 *
 * Operaciones (`usuario.empresa` nulo) ve el alcance completo de siempre.
 * Un `CLIENT_ADMIN`/`CLIENT_USER` (ADR-0004: comparten exactamente el mismo
 * permiso `users.manage`, con alcance a su empresa) ve solo su empresa, sin
 * selector y sin roles internos — el backend lo rechazaría igual, pero acá
 * ni tiene sentido ofrecerlo.
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
      <GestionUsuarios
        modo={{
          alcance: "propia",
          empresaId: usuario.empresa.id,
          nombreEmpresa: usuario.empresa.trade_name || usuario.empresa.legal_name,
        }}
      />
    );
  }

  return <GestionUsuarios modo={{ alcance: "completo", empresaFiltro }} />;
}
