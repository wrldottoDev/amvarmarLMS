"use client";

import { UserRound } from "lucide-react";
import { useState } from "react";
import { FormularioContrasena } from "@/components/auth/formulario-contrasena";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { useActualizarPerfil } from "@/features/auth/cuenta";
import { useSesion } from "@/features/auth/contexto-sesion";

export default function PaginaCuenta() {
  const { usuario } = useSesion();
  const actualizar = useActualizarPerfil();

  const [nombre, setNombre] = useState(usuario?.first_name ?? "");
  const [apellido, setApellido] = useState(usuario?.last_name ?? "");
  const [guardado, setGuardado] = useState(false);

  if (!usuario) return null;

  return (
    <section className="mx-auto max-w-2xl space-y-6">
      <header>
        <h1 className="text-xl font-bold">Mi cuenta</h1>
        <p className="mt-0.5 text-sm text-[var(--texto-secundario)]">
          Tus datos y tu contraseña.
        </p>
      </header>

      <div className="space-y-4 rounded-md border bg-[var(--superficie)] px-4 py-4">
        <div className="flex items-center gap-2">
          <UserRound className="size-5 text-[var(--marca)]" aria-hidden="true" />
          <h2 className="text-base font-bold">Datos</h2>
        </div>

        <form
          className="space-y-3"
          onSubmit={async (evento) => {
            evento.preventDefault();
            await actualizar.mutateAsync({ first_name: nombre, last_name: apellido });
            setGuardado(true);
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-sm font-medium">Nombre</span>
              <input
                className="w-full rounded-md border px-3 py-2 text-sm"
                value={nombre}
                onChange={(evento) => setNombre(evento.target.value)}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-sm font-medium">Apellido</span>
              <input
                className="w-full rounded-md border px-3 py-2 text-sm"
                value={apellido}
                onChange={(evento) => setApellido(evento.target.value)}
              />
            </label>
          </div>

          <div>
            <span className="mb-1 block text-sm font-medium">Correo</span>
            <p className="rounded-md border bg-[var(--hover)] px-3 py-2 text-sm text-[var(--texto-secundario)]">
              {usuario.email}
            </p>
            {/* El correo es con lo que entra: cambiarlo sin verificar el nuevo
                dejaría la cuenta sin forma de recuperarse. */}
            <span className="mt-1 block text-xs text-[var(--texto-secundario)]">
              Para cambiarlo, pedíselo a Operaciones.
            </span>
          </div>

          {actualizar.error ? <AvisoError error={actualizar.error} /> : null}
          {guardado ? (
            <p className="text-sm text-[var(--exito)]">Datos guardados.</p>
          ) : null}

          <Boton type="submit" cargando={actualizar.isPending}>
            Guardar cambios
          </Boton>
        </form>
      </div>

      <div className="space-y-4 rounded-md border bg-[var(--superficie)] px-4 py-4">
        <h2 className="text-base font-bold">Contraseña</h2>
        <FormularioContrasena />
      </div>
    </section>
  );
}
