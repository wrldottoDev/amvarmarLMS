"use client";

import { ShieldAlert } from "lucide-react";
import { useRouter } from "next/navigation";
import { ContenedorAuth } from "@/components/auth/contenedor-auth";
import { FormularioContrasena } from "@/components/auth/formulario-contrasena";

/**
 * Bloqueo por contraseña temporal.
 *
 * Quien entra con una contraseña que le dio un administrador no puede usar el
 * sistema hasta cambiarla: esa contraseña la conoce alguien más. El servidor lo
 * impone; esta pantalla solo lo explica y ofrece la salida.
 */
export default function PaginaCambiarContrasena() {
  const router = useRouter();

  return (
    <ContenedorAuth>
      <h1 className="text-2xl font-bold">Cambiá tu contraseña</h1>
      <p className="mb-4 mt-1 text-sm text-[var(--texto-secundario)]">
        Es el único paso que falta para empezar.
      </p>

      <div className="mb-4 flex gap-2.5 rounded-md border border-[#f2d9a0] bg-[#fff6e5] px-3 py-2.5 text-sm text-[#8a5b00]">
        <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <p>
          Entraste con una contraseña temporal que te dieron. Elegí una propia: la temporal la
          conoce quien creó tu cuenta.
        </p>
      </div>

      <FormularioContrasena alTerminar={() => router.replace("/dashboard")} />
    </ContenedorAuth>
  );
}
