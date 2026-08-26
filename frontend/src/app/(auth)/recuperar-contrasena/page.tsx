"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowLeft, Mail } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { Campo } from "@/components/ui/campo";
import { api, exigirDatos } from "@/lib/api/client";

const esquema = z.object({ email: z.string().trim().email("Ingresá un correo válido.") });
type DatosRecuperacion = z.infer<typeof esquema>;

export default function PaginaRecuperarPassword() {
  const [mensaje, setMensaje] = useState<string | null>(null);
  const [errorSolicitud, setErrorSolicitud] = useState<unknown>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<DatosRecuperacion>({ resolver: zodResolver(esquema) });

  async function enviar(datos: DatosRecuperacion) {
    setErrorSolicitud(null);
    try {
      const resultado = await api.POST("/api/v1/auth/password/forgot", { body: datos });
      setMensaje(exigirDatos(resultado).mensaje);
    } catch (error) {
      setErrorSolicitud(error);
    }
  }

  return (
    <>
      <Link className="mb-7 inline-flex items-center gap-2 text-sm font-semibold text-[var(--mar)] hover:underline" href="/login">
        <ArrowLeft className="size-4" aria-hidden="true" />
        Volver
      </Link>
      <h1 className="text-2xl font-bold">Recuperar contraseña</h1>
      <p className="mt-2 text-sm text-[var(--texto-secundario)]">Ingresá el correo asociado a tu cuenta.</p>

      {mensaje ? (
        <div className="mt-7 rounded-md border border-[var(--exito-borde)] bg-[var(--exito-tenue)] px-4 py-4 text-sm text-[var(--exito)]" role="status">
          {mensaje}
        </div>
      ) : (
        <form className="mt-7 grid gap-5" onSubmit={handleSubmit(enviar)} noValidate>
          {errorSolicitud ? <AvisoError error={errorSolicitud} /> : null}
          <Campo
            etiqueta="Correo electrónico"
            type="email"
            autoComplete="email"
            placeholder="nombre@empresa.com"
            error={errors.email?.message}
            {...register("email")}
          />
          <Boton type="submit" cargando={isSubmitting} className="w-full">
            <Mail className="size-4" aria-hidden="true" />
            Enviar instrucciones
          </Boton>
        </form>
      )}
    </>
  );
}
