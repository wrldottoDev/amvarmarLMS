"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import Link from "next/link";
import { use } from "react";
import { useForm } from "react-hook-form";
import { useState } from "react";
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

type DatosInvitacion = z.infer<typeof esquema>;

/**
 * Alta por invitación.
 *
 * Reemplaza el correo de credenciales del sistema anterior, que mandaba usuario
 * y contraseña en texto plano. Acá la persona llega con un enlace de un solo uso
 * y elige su propia contraseña: nadie más la conoce, ni siquiera quien creó la
 * cuenta.
 */
export default function PaginaInvitacion({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = use(params);
  const [listo, setListo] = useState(false);
  const [errorSolicitud, setErrorSolicitud] = useState<unknown>(null);

  // Se consulta antes de pedir nada para que la persona vea a qué cuenta está
  // entrando. Consultarlo no consume el enlace, así que recargar es seguro.
  const invitacion = useQuery({
    queryKey: ["invitacion", token],
    queryFn: async () =>
      exigirDatos(await api.GET("/api/v1/auth/invitation/{token}", { params: { path: { token } } })),
    retry: false,
  });

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<DatosInvitacion>({ resolver: zodResolver(esquema) });

  async function enviar(datos: DatosInvitacion) {
    setErrorSolicitud(null);
    try {
      await api.POST("/api/v1/auth/invitation/accept", {
        body: { token, nueva_password: datos.password },
      });
      setListo(true);
    } catch (error) {
      setErrorSolicitud(error);
    }
  }

  if (invitacion.isPending) {
    return <p className="text-sm text-[var(--texto-secundario)]">Comprobando el enlace…</p>;
  }

  if (invitacion.isError) {
    return (
      <>
        <h1 className="text-2xl font-bold">Este enlace ya no sirve</h1>
        <p className="mt-3 text-sm text-[var(--texto-secundario)]">
          Los enlaces de invitación vencen a las 48 horas y se pueden usar una sola vez. Pídale
          uno nuevo a quien le creó la cuenta.
        </p>
        <Link className="mt-6 inline-block text-sm font-semibold text-[var(--mar)] hover:underline" href="/login">
          Ir a iniciar sesión
        </Link>
      </>
    );
  }

  if (listo) {
    return (
      <div className="grid gap-4 rounded-md border border-[var(--exito-borde)] bg-[var(--exito-tenue)] px-4 py-4 text-sm text-[var(--exito)]" role="status">
        <p>Su contraseña quedó lista. Ya puede entrar al sistema.</p>
        <Link className="font-semibold underline" href="/login">
          Iniciar sesión
        </Link>
      </div>
    );
  }

  const { first_name, last_name, email } = invitacion.data;

  return (
    <>
      <h1 className="text-2xl font-bold">
        Bienvenido, {first_name} {last_name}
      </h1>
      <p className="mt-2 text-sm text-[var(--texto-secundario)]">
        Su cuenta es <span className="font-medium text-[var(--texto)]">{email}</span>. Elija una
        contraseña para terminar de activarla.
      </p>

      <form className="mt-7 grid gap-5" onSubmit={handleSubmit(enviar)} noValidate>
        {errorSolicitud ? <AvisoError error={errorSolicitud} /> : null}
        <Campo
          etiqueta="Contraseña"
          type="password"
          autoComplete="new-password"
          ayuda="Al menos 12 caracteres. Una frase que recuerde es mejor que algo corto y raro."
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
          Activar mi cuenta
        </Boton>
      </form>
    </>
  );
}
