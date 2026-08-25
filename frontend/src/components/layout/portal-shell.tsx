"use client";

import {
  Boxes,
  ChevronDown,
  Gauge,
  LogOut,
  Menu,
  MonitorSmartphone,
  PanelLeftClose,
  Settings,
  X,
} from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { useSesion } from "@/features/auth/contexto-sesion";
import { clases, iniciales } from "@/lib/utilidades";

interface ElementoNavegacion {
  href: string;
  etiqueta: string;
  icono: typeof Gauge;
}

export function PortalShell({ children }: Readonly<{ children: React.ReactNode }>) {
  const [menuMovil, setMenuMovil] = useState(false);
  const [menuUsuario, setMenuUsuario] = useState(false);
  const { usuario, cerrarSesion } = useSesion();
  const pathname = usePathname();
  const router = useRouter();

  if (!usuario) return null;

  const esCliente = Boolean(usuario.empresa);
  const navegacion: ElementoNavegacion[] = [
    {
      href: esCliente ? "/dashboard" : "/operaciones",
      etiqueta: esCliente ? "Resumen" : "Operaciones",
      icono: Gauge,
    },
    { href: "/shipments", etiqueta: "Cargas", icono: Boxes },
    { href: "/sesiones", etiqueta: "Sesiones", icono: MonitorSmartphone },
  ];

  async function salir() {
    await cerrarSesion();
    router.replace("/login");
  }

  const barra = (
    <>
      <div className="flex h-20 items-center justify-between border-b border-white/10 px-5">
        <Link href={esCliente ? "/dashboard" : "/operaciones"} className="flex items-center gap-3" onClick={() => setMenuMovil(false)}>
          <Image className="h-10 w-auto" src="/brand/amvarmar-isotipo.png" alt="AMVARMAR" width={26} height={40} priority />
          <span>
            <strong className="block text-sm text-white">AMVARMAR</strong>
            <span className="block text-[11px] text-white/55">LMS</span>
          </span>
        </Link>
        <button
          type="button"
          className="grid size-9 place-items-center rounded-md text-white/70 hover:bg-white/10 lg:hidden"
          onClick={() => setMenuMovil(false)}
          aria-label="Cerrar navegación"
          title="Cerrar navegación"
        >
          <X className="size-5" />
        </button>
      </div>

      <nav className="flex-1 space-y-1 px-3 py-5" aria-label="Navegación principal">
        {navegacion.map((elemento) => {
          const activo = pathname === elemento.href || (elemento.href === "/shipments" && pathname.startsWith("/shipments/"));
          const Icono = elemento.icono;
          return (
            <Link
              key={elemento.href}
              href={elemento.href}
              onClick={() => setMenuMovil(false)}
              className={clases(
                "flex h-11 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors",
                activo ? "bg-white text-[var(--mar-oscuro)]" : "text-white/72 hover:bg-white/8 hover:text-white",
              )}
            >
              <Icono className="size-4" aria-hidden="true" />
              {elemento.etiqueta}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-white/10 px-5 py-4 text-xs text-white/50">
        <p className="truncate">{usuario.empresa?.trade_name ?? usuario.empresa?.legal_name ?? "AMVARMAR Operaciones"}</p>
      </div>
    </>
  );

  return (
    <div className="min-h-screen bg-[var(--fondo)]">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col bg-[var(--mar-oscuro)] lg:flex">{barra}</aside>

      {menuMovil ? (
        <div className="fixed inset-0 z-40 bg-[#11181c]/50 lg:hidden" onMouseDown={() => setMenuMovil(false)}>
          <aside className="flex h-full w-[min(86vw,280px)] flex-col bg-[var(--mar-oscuro)]" onMouseDown={(evento) => evento.stopPropagation()}>
            {barra}
          </aside>
        </div>
      ) : null}

      <div className="lg:pl-60">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b bg-white/95 px-4 backdrop-blur sm:px-6 lg:px-8">
          <button
            type="button"
            className="grid size-10 place-items-center rounded-md text-[var(--texto-secundario)] hover:bg-[#edf1f2] lg:hidden"
            onClick={() => setMenuMovil(true)}
            aria-label="Abrir navegación"
            title="Abrir navegación"
          >
            <Menu className="size-5" />
          </button>

          <div className="hidden items-center gap-2 text-xs font-medium text-[var(--texto-secundario)] lg:flex">
            <PanelLeftClose className="size-4" aria-hidden="true" />
            {esCliente ? "Portal del cliente" : "Panel de operaciones"}
          </div>

          <div className="relative ml-auto">
            <button
              type="button"
              className="flex h-11 items-center gap-2 rounded-md px-2 text-left hover:bg-[#edf1f2]"
              onClick={() => setMenuUsuario((valor) => !valor)}
              aria-expanded={menuUsuario}
            >
              <span className="grid size-8 place-items-center rounded-md bg-[#e8f0f2] text-xs font-bold text-[var(--mar)]">
                {iniciales(usuario.first_name, usuario.last_name)}
              </span>
              <span className="hidden max-w-40 sm:block">
                <strong className="block truncate text-sm">{usuario.first_name} {usuario.last_name}</strong>
                <span className="block truncate text-xs font-normal text-[var(--texto-secundario)]">{usuario.email}</span>
              </span>
              <ChevronDown className="size-4 text-[var(--texto-secundario)]" aria-hidden="true" />
            </button>

            {menuUsuario ? (
              <div className="absolute right-0 mt-2 w-56 rounded-md border bg-white p-1.5 shadow-xl">
                <Link
                  href="/sesiones"
                  className="flex items-center gap-2 rounded px-3 py-2 text-sm hover:bg-[#edf1f2]"
                  onClick={() => setMenuUsuario(false)}
                >
                  <Settings className="size-4" aria-hidden="true" />
                  Seguridad de la cuenta
                </Link>
                <button
                  type="button"
                  className="flex w-full items-center gap-2 rounded px-3 py-2 text-sm text-[var(--peligro)] hover:bg-[#fff2f0]"
                  onClick={() => void salir()}
                >
                  <LogOut className="size-4" aria-hidden="true" />
                  Cerrar sesión
                </button>
              </div>
            ) : null}
          </div>
        </header>

        <main className="mx-auto w-full max-w-[1500px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">{children}</main>
      </div>
    </div>
  );
}
