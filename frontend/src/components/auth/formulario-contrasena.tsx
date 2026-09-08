"use client";

import { KeyRound } from "lucide-react";
import { useState } from "react";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { useCambiarContrasena } from "@/features/auth/cuenta";

const MINIMO = 12;

/**
 * Cambio de contraseña.
 *
 * Se usa en dos lugares: en los ajustes de la cuenta, y en la pantalla que
 * bloquea a quien todavía tiene una contraseña temporal. El formulario es el
 * mismo porque el acto es el mismo; solo cambia el texto que lo acompaña.
 */
export function FormularioContrasena({ alTerminar }: { alTerminar?: () => void }) {
  const [actual, setActual] = useState("");
  const [nueva, setNueva] = useState("");
  const [repetida, setRepetida] = useState("");
  const [listo, setListo] = useState(false);

  const cambiar = useCambiarContrasena();

  const coinciden = nueva === repetida;
  const suficiente = nueva.length >= MINIMO;
  const puede = actual.length > 0 && suficiente && coinciden && nueva !== actual;

  async function enviar() {
    await cambiar.mutateAsync({ actual, nueva });
    setActual("");
    setNueva("");
    setRepetida("");
    setListo(true);
    alTerminar?.();
  }

  return (
    <form
      className="space-y-3"
      onSubmit={(evento) => {
        evento.preventDefault();
        if (puede) void enviar();
      }}
    >
      <label className="block">
        <span className="mb-1 block text-sm font-medium">Contraseña actual</span>
        <input
          type="password"
          autoComplete="current-password"
          className="w-full rounded-md border px-3 py-2 text-sm"
          value={actual}
          onChange={(evento) => setActual(evento.target.value)}
        />
      </label>

      <label className="block">
        <span className="mb-1 block text-sm font-medium">Contraseña nueva</span>
        <input
          type="password"
          autoComplete="new-password"
          className="w-full rounded-md border px-3 py-2 text-sm"
          value={nueva}
          onChange={(evento) => setNueva(evento.target.value)}
        />
        <span className="mt-1 block text-xs text-[var(--texto-secundario)]">
          Al menos {MINIMO} caracteres. Una frase que recuerdes es mejor que algo corto y raro.
        </span>
      </label>

      <label className="block">
        <span className="mb-1 block text-sm font-medium">Repetila</span>
        <input
          type="password"
          autoComplete="new-password"
          className="w-full rounded-md border px-3 py-2 text-sm"
          value={repetida}
          onChange={(evento) => setRepetida(evento.target.value)}
        />
        {repetida && !coinciden ? (
          <span className="mt-1 block text-xs text-[var(--peligro)]">
            Las dos contraseñas no son iguales.
          </span>
        ) : null}
      </label>

      {cambiar.error ? <AvisoError error={cambiar.error} /> : null}

      {listo ? (
        <p className="rounded-md border border-[var(--exito-borde)] bg-[var(--exito-tenue)] px-3 py-2.5 text-sm text-[var(--exito)]">
          Contraseña actualizada. Las demás sesiones se cerraron.
        </p>
      ) : null}

      <Boton type="submit" disabled={!puede} cargando={cambiar.isPending}>
        <KeyRound className="size-4" aria-hidden="true" />
        Cambiar contraseña
      </Boton>
    </form>
  );
}
