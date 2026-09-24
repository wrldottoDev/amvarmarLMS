"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { EVENTO_CAMBIO_OBLIGATORIO } from "@/lib/api/client";

/**
 * Lleva a cambiar la contraseña cuando el servidor lo exige.
 *
 * Escucha el evento que emite el cliente HTTP. Va acá y no dentro del cliente
 * porque ahí no hay router, y navegar con `window.location` recargaría toda la
 * aplicación perdiendo el token, que vive solo en memoria.
 */
export function GuardiaContrasena() {
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    function alBloquear() {
      if (pathname !== "/cambiar-contrasena") {
        router.replace("/cambiar-contrasena");
      }
    }
    window.addEventListener(EVENTO_CAMBIO_OBLIGATORIO, alBloquear);
    return () => window.removeEventListener(EVENTO_CAMBIO_OBLIGATORIO, alBloquear);
  }, [router, pathname]);

  return null;
}
