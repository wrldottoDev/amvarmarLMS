"use client";

import { Check, Copy, KeyRound } from "lucide-react";
import { useState } from "react";

/**
 * Muestra la contraseña temporal recién generada.
 *
 * Se ve UNA sola vez: no se guarda en claro en ningún lado y no hay forma de
 * volver a consultarla. Por eso el aviso es explícito y el botón de copiar está
 * a la mano — si se cierra sin copiarla, hay que generar otra.
 */
export function ContrasenaTemporal({
  correo,
  contrasena,
}: {
  correo: string;
  contrasena: string;
}) {
  const [copiado, setCopiado] = useState(false);

  async function copiar() {
    await navigator.clipboard.writeText(contrasena);
    setCopiado(true);
    setTimeout(() => setCopiado(false), 2000);
  }

  return (
    <div className="space-y-3 rounded-md border border-[#b7e0c2] bg-[#e9f6ec] px-4 py-4">
      <p className="flex items-center gap-2 text-sm font-semibold text-[#1c6b33]">
        <KeyRound className="size-4" aria-hidden="true" />
        Cuenta creada para {correo}
      </p>

      <div className="flex items-center gap-2">
        <code className="flex-1 overflow-x-auto rounded border bg-white px-3 py-2 font-mono text-sm">
          {contrasena}
        </code>
        <button
          type="button"
          className="flex h-10 shrink-0 items-center gap-1.5 rounded-md border bg-white px-3 text-sm font-medium hover:bg-[#f0f3f4]"
          onClick={() => void copiar()}
        >
          {copiado ? (
            <>
              <Check className="size-4" aria-hidden="true" />
              Copiada
            </>
          ) : (
            <>
              <Copy className="size-4" aria-hidden="true" />
              Copiar
            </>
          )}
        </button>
      </div>

      <p className="text-sm text-[#1c6b33]">
        Pasásela por un medio seguro. <strong>No se va a volver a mostrar</strong>, y la persona
        tendrá que cambiarla la primera vez que entre.
      </p>
    </div>
  );
}
