"use client";

import { MailCheck } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { api, exigirDatos } from "@/lib/api/client";

/**
 * Se confirma con un botón y no al cargar: los filtros de correo abren los
 * enlaces para revisarlos, y un canje automático quemaría el token antes de que
 * la persona llegue.
 */
function ConfirmarCorreo() {
  const parametros = useSearchParams();
  const token = parametros.get("token") ?? "";
  const [mensaje, setMensaje] = useState<string | null>(null);
  const [errorSolicitud, setErrorSolicitud] = useState<unknown>(null);
  const [enviando, setEnviando] = useState(false);

  async function confirmar() {
    setErrorSolicitud(null);
    setEnviando(true);
    try {
      const resultado = await api.POST("/api/v1/auth/email/verify/confirm", {
        body: { token },
      });
      setMensaje(exigirDatos(resultado).mensaje);
    } catch (error) {
      setErrorSolicitud(error);
    } finally {
      setEnviando(false);
    }
  }

  if (!token) {
    return <AvisoError error={new Error("El enlace de verificación no es válido.")} />;
  }

  return (
    <>
      <h1 className="text-2xl font-bold">Confirmar correo</h1>

      {mensaje ? (
        <div
          className="mt-7 grid gap-4 rounded-md border border-[var(--exito-borde)] bg-[var(--exito-tenue)] px-4 py-4 text-sm text-[var(--exito)]"
          role="status"
        >
          <p>{mensaje}</p>
          <Link className="font-semibold underline" href="/login">
            Ir al sistema
          </Link>
        </div>
      ) : (
        <div className="mt-7 grid gap-5">
          <p className="text-sm text-[var(--texto-secundario)]">
            Confirme que esta dirección es suya para recibir los avisos de AMVARMAR por correo.
          </p>
          {errorSolicitud ? <AvisoError error={errorSolicitud} /> : null}
          <Boton type="button" cargando={enviando} className="w-full" onClick={confirmar}>
            <MailCheck className="size-4" aria-hidden="true" />
            Confirmar mi correo
          </Boton>
        </div>
      )}
    </>
  );
}

export default function PaginaVerificarCorreo() {
  return (
    <Suspense>
      <ConfirmarCorreo />
    </Suspense>
  );
}
