"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowLeft, KeyRound } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { Campo } from "@/components/ui/campo";
import { api, exigirDatos } from "@/lib/api/client";

const esquema = z
  .object({
    password: z.string().min(12, "La contraseña debe tener al menos 12 caracteres."),
    confirmar: z.string(),
  })
  .refine((datos) => datos.password === datos.confirmar, {
    message: "Las contraseñas no coinciden.",
    path: ["confirmar"],
  });

type DatosReset = z.infer<typeof esquema>;

function FormularioReset() {
  const parametros = useSearchParams();
  const token = parametros.get("token") ?? "";
  const [mensaje, setMensaje] = useState<string | null>(null);
  const [errorSolicitud, setErrorSolicitud] = useState<unknown>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<DatosReset>({ resolver: zodResolver(esquema) });

  async function enviar(datos: DatosReset) {
    setErrorSolicitud(null);
    try {
      const resultado = await api.POST("/api/v1/auth/password/reset", {
        body: { token, nueva_password: datos.password },
      });
      setMensaje(exigirDatos(resultado).mensaje);
    } catch (error) {
      setErrorSolicitud(error);
    }
  }

  if (!token) {
    return <AvisoError error={new Error("El enlace de recuperación no es válido.")} />;
  }

  return (
    <>
      <Link className="mb-7 inline-flex items-center gap-2 text-sm font-semibold text-[var(--mar)] hover:underline" href="/login">
        <ArrowLeft className="size-4" aria-hidden="true" />
        Volver
      </Link>
      <h1 className="text-2xl font-bold">Nueva contraseña</h1>

      {mensaje ? (
        <div className="mt-7 grid gap-4 rounded-md border border-[var(--exito-borde)] bg-[var(--exito-tenue)] px-4 py-4 text-sm text-[var(--exito)]" role="status">
          <p>{mensaje}</p>
          <Link className="font-semibold underline" href="/login">Iniciar sesión</Link>
        </div>
      ) : (
        <form className="mt-7 grid gap-5" onSubmit={handleSubmit(enviar)} noValidate>
          {errorSolicitud ? <AvisoError error={errorSolicitud} /> : null}
          <Campo
            etiqueta="Nueva contraseña"
            type="password"
            autoComplete="new-password"
            error={errors.password?.message}
            {...register("password")}
          />
          <Campo
            etiqueta="Confirmar contraseña"
            type="password"
            autoComplete="new-password"
            error={errors.confirmar?.message}
            {...register("confirmar")}
          />
          <Boton type="submit" cargando={isSubmitting} className="w-full">
            <KeyRound className="size-4" aria-hidden="true" />
            Actualizar contraseña
          </Boton>
        </form>
      )}
    </>
  );
}

export default function PaginaRestablecerPassword() {
  return (
    <Suspense>
      <FormularioReset />
    </Suspense>
  );
}
