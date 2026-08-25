"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { Eye, EyeOff, LogIn } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { Campo } from "@/components/ui/campo";
import { useSesion } from "@/features/auth/contexto-sesion";

const esquema = z.object({
  email: z.string().trim().email("Ingresá un correo válido."),
  password: z.string().min(1, "Ingresá tu contraseña."),
});

type DatosLogin = z.infer<typeof esquema>;

function FormularioLogin() {
  const [mostrarPassword, setMostrarPassword] = useState(false);
  const [errorSolicitud, setErrorSolicitud] = useState<unknown>(null);
  const { iniciarSesion, estado, usuario } = useSesion();
  const router = useRouter();
  const parametros = useSearchParams();
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<DatosLogin>({ resolver: zodResolver(esquema) });

  useEffect(() => {
    if (estado === "autenticada" && usuario) {
      const regreso = parametros.get("regreso");
      const destinoPredeterminado = usuario.empresa ? "/dashboard" : "/operaciones";
      const destino = regreso?.startsWith("/") && !regreso.startsWith("//") ? regreso : destinoPredeterminado;
      router.replace(destino);
    }
  }, [estado, parametros, router, usuario]);

  async function enviar(datos: DatosLogin) {
    setErrorSolicitud(null);
    try {
      await iniciarSesion(datos.email, datos.password);
    } catch (error) {
      setErrorSolicitud(error);
    }
  }

  return (
    <>
      <div className="mb-7">
        <h1 className="text-2xl font-bold text-[var(--texto)]">Iniciar sesión</h1>
        <p className="mt-2 text-sm text-[var(--texto-secundario)]">Ingresá a tu cuenta de AMVARMAR.</p>
      </div>

      <form className="grid gap-5" onSubmit={handleSubmit(enviar)} noValidate>
        {errorSolicitud ? <AvisoError error={errorSolicitud} /> : null}
        <Campo
          etiqueta="Correo electrónico"
          type="email"
          autoComplete="email"
          placeholder="nombre@empresa.com"
          error={errors.email?.message}
          {...register("email")}
        />

        <div className="relative">
          <Campo
            etiqueta="Contraseña"
            type={mostrarPassword ? "text" : "password"}
            autoComplete="current-password"
            className="pr-12"
            error={errors.password?.message}
            {...register("password")}
          />
          <button
            type="button"
            className="absolute right-1.5 top-[29px] grid size-9 place-items-center rounded-md text-[var(--texto-secundario)] hover:bg-[#edf1f2]"
            onClick={() => setMostrarPassword((valor) => !valor)}
            title={mostrarPassword ? "Ocultar contraseña" : "Mostrar contraseña"}
            aria-label={mostrarPassword ? "Ocultar contraseña" : "Mostrar contraseña"}
          >
            {mostrarPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
          </button>
        </div>

        <div className="flex justify-end">
          <Link className="text-sm font-semibold text-[var(--mar)] hover:underline" href="/recuperar-contrasena">
            ¿Olvidaste tu contraseña?
          </Link>
        </div>

        <Boton type="submit" cargando={isSubmitting} className="w-full">
          <LogIn className="size-4" aria-hidden="true" />
          Ingresar
        </Boton>
      </form>
    </>
  );
}

export default function PaginaLogin() {
  return (
    <Suspense>
      <FormularioLogin />
    </Suspense>
  );
}
