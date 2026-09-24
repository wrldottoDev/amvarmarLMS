"use client";

import { Check, Copy, KeyRound, Mail, TriangleAlert } from "lucide-react";
import { useState } from "react";

/**
 * En qué quedó una contraseña recién generada.
 *
 * Tres situaciones distintas, no dos: el alta con invitación entregada, el alta
 * con el correo caído, y el restablecimiento que hace un administrador —donde
 * no hay invitación ninguna y la temporal siempre es la vía—.
 *
 * Cuando la invitación salió, la temporal es solo un respaldo y arranca
 * plegada: mostrarla de entrada empuja a repartir contraseñas por chat cuando
 * no hace falta. En los tres casos se ve UNA vez; no se guarda en claro en
 * ningún lado y no hay forma de volver a consultarla.
 */
export type EstadoInvitacion = "enviada" | "fallo" | "no-aplica";

const TONO = {
  enviada: {
    caja: "border-[var(--exito-borde)] bg-[var(--exito-tenue)]",
    texto: "text-[var(--exito)]",
  },
  fallo: {
    caja: "border-[var(--advertencia-borde)] bg-[var(--advertencia-tenue)]",
    texto: "text-[var(--advertencia)]",
  },
  "no-aplica": {
    caja: "border-[var(--exito-borde)] bg-[var(--exito-tenue)]",
    texto: "text-[var(--exito)]",
  },
} as const;

export function ContrasenaTemporal({
  correo,
  contrasena,
  invitacion,
}: {
  correo: string;
  contrasena: string;
  invitacion: EstadoInvitacion;
}) {
  const [copiado, setCopiado] = useState(false);
  // Plegada solo cuando hay otra vía de acceso viva.
  const [verContrasena, setVerContrasena] = useState(invitacion !== "enviada");

  const tono = TONO[invitacion];

  async function copiar() {
    await navigator.clipboard.writeText(contrasena);
    setCopiado(true);
    setTimeout(() => setCopiado(false), 2000);
  }

  return (
    <div className={`space-y-3 rounded-md border px-4 py-4 ${tono.caja}`}>
      <p className={`flex items-center gap-2 text-sm font-semibold ${tono.texto}`}>
        {invitacion === "enviada" ? (
          <Mail className="size-4" aria-hidden="true" />
        ) : invitacion === "fallo" ? (
          <TriangleAlert className="size-4" aria-hidden="true" />
        ) : (
          <KeyRound className="size-4" aria-hidden="true" />
        )}
        {invitacion === "no-aplica" ? "Contraseña restablecida" : "Cuenta creada"} para {correo}
      </p>

      {invitacion === "enviada" ? (
        <p className={`text-sm ${tono.texto}`}>
          Le mandamos un enlace para que elija su contraseña. Vence en 48 horas y sirve una sola
          vez.
        </p>
      ) : invitacion === "fallo" ? (
        <p className={`text-sm ${tono.texto}`}>
          <strong>El correo de invitación no salió.</strong> Esta contraseña temporal es el único
          acceso que tiene esa persona ahora mismo.
        </p>
      ) : (
        <p className={`text-sm ${tono.texto}`}>
          Sus sesiones abiertas se cerraron. Necesita esta contraseña para volver a entrar.
        </p>
      )}

      {verContrasena ? (
        <>
          <div className="flex items-center gap-2">
            <code className="flex-1 overflow-x-auto rounded border bg-[var(--superficie)] px-3 py-2 font-mono text-sm">
              {contrasena}
            </code>
            <button
              type="button"
              className="flex h-10 shrink-0 items-center gap-1.5 rounded-md border bg-[var(--superficie)] px-3 text-sm font-medium hover:bg-[var(--hover)]"
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

          <p className="text-sm text-[var(--texto-secundario)]">
            Pasásela por un medio seguro. <strong>No se va a volver a mostrar</strong>, y la
            persona tendrá que cambiarla la primera vez que entre.
          </p>
        </>
      ) : (
        <button
          type="button"
          className={`inline-flex items-center gap-1.5 text-sm font-semibold underline ${tono.texto}`}
          onClick={() => setVerContrasena(true)}
        >
          <KeyRound className="size-4" aria-hidden="true" />
          Ver la contraseña temporal de respaldo
        </button>
      )}
    </div>
  );
}
