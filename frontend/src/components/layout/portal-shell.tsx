"use client";

import {
  Bell,
  Boxes,
  Building2,
  ChevronDown,
  Gauge,
  LogOut,
  Menu,
  MonitorSmartphone,
  Moon,
  Settings,
  Sun,
  Truck,
  UserRound,
  UsersRound,
  Warehouse,
  X,
} from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { AmviChat } from "@/components/copilot/amvi-chat";
import { CampanaNotificaciones } from "@/components/notificaciones/campana";
import { useSesion } from "@/features/auth/contexto-sesion";
import { useSidebar, useTema } from "@/features/preferencias/apariencia";
import { clases, iniciales } from "@/lib/utilidades";

interface ElementoNavegacion {
  href: string;
  etiqueta: string;
  icono: typeof Gauge;
}

/**
 * El esqueleto del sistema anterior.
 *
 * Barra superior de 56px a todo el ancho, sidebar fijo debajo que va de 72 a
 * 260 px, y el contenido corrido a la derecha. Es la disposición de
 * `templates/admin/base_admin.html` del WMS de Django, con las mismas medidas:
 * el equipo lleva años con esta pantalla y la reconoce sin leerla.
 *
 * En móvil el sidebar no se pliega, se superpone entero: 72px de solo iconos en
 * una pantalla angosta no dice qué es cada cosa.
 */
export function PortalShell({ children }: Readonly<{ children: React.ReactNode }>) {
  const [menuMovil, setMenuMovil] = useState(false);
  const [menuUsuario, setMenuUsuario] = useState(false);
  const { usuario, cerrarSesion } = useSesion();
  const { expandido, alternar: alternarSidebar } = useSidebar();
  const { tema, alternar: alternarTema } = useTema();
  const pathname = usePathname();
  const router = useRouter();

  if (!usuario) return null;

  const esCliente = Boolean(usuario.empresa);
  const inicio = esCliente ? "/dashboard" : "/operaciones";

  const navegacion: ElementoNavegacion[] = [
    { href: inicio, etiqueta: esCliente ? "Resumen" : "Operaciones", icono: Gauge },
    { href: "/shipments", etiqueta: "Cargas", icono: Boxes },
    { href: "/despachos", etiqueta: "Despachos", icono: Truck },
    { href: "/avisos", etiqueta: "Avisos", icono: Bell },
    ...(esCliente
      ? [{ href: "/usuarios", etiqueta: "Usuarios", icono: UsersRound }]
      : [
          { href: "/inventario", etiqueta: "Inventario", icono: Warehouse },
          { href: "/empresas", etiqueta: "Empresas", icono: Building2 },
          { href: "/usuarios", etiqueta: "Usuarios", icono: UsersRound },
        ]),
    { href: "/sesiones", etiqueta: "Sesiones", icono: MonitorSmartphone },
  ];

  async function salir() {
    await cerrarSesion();
    router.replace("/login");
  }

  // `abierto` decide si se ve el texto: en escritorio manda la preferencia
  // guardada; en el panel móvil siempre se ve, porque ahí se superpone completo.
  function enlaces(abierto: boolean) {
    return navegacion.map((elemento) => {
      const activo =
        pathname === elemento.href ||
        (elemento.href !== "/" && pathname.startsWith(`${elemento.href}/`));
      const Icono = elemento.icono;

      return (
        <Link
          key={elemento.href}
          href={elemento.href}
          onClick={() => setMenuMovil(false)}
          title={abierto ? undefined : elemento.etiqueta}
          aria-current={activo ? "page" : undefined}
          className={clases(
            "flex items-center gap-3 overflow-hidden whitespace-nowrap rounded-lg px-3 py-2.5 text-sm transition-colors",
            activo
              ? "bg-[var(--marca-tenue)] font-bold text-[var(--marca)]"
              : "text-[var(--texto)] hover:bg-[var(--hover)]",
          )}
        >
          <Icono
            className={clases(
              "size-5 shrink-0",
              activo ? "text-[var(--marca)]" : "text-[var(--texto-secundario)]",
            )}
            aria-hidden="true"
          />
          {/* Se oculta con CSS y no se desmonta: así el enlace conserva su
              nombre accesible cuando el sidebar está plegado. */}
          <span className={abierto ? "" : "sr-only"}>{elemento.etiqueta}</span>
        </Link>
      );
    });
  }

  return (
    <div className="min-h-screen bg-[var(--fondo)]">
      <header className="fixed inset-x-0 top-0 z-40 flex h-[var(--header-h)] items-center gap-1 border-b bg-[var(--superficie)] px-2 sm:px-3">
        <button
          type="button"
          className="grid size-10 place-items-center rounded-lg text-[var(--texto)] hover:bg-[var(--hover)]"
          onClick={() => (window.innerWidth < 992 ? setMenuMovil(true) : alternarSidebar())}
          aria-expanded={expandido}
          aria-label="Mostrar u ocultar el menú"
          title="Mostrar u ocultar el menú"
        >
          <Menu className="size-5" />
        </button>

        <Link href={inicio} className="flex items-center gap-2 px-1">
          <Image
            className="h-7 w-auto"
            src="/brand/amvarmar-isotipo.png"
            alt=""
            width={20}
            height={30}
            priority
          />
          <span className="text-lg font-extrabold text-[var(--mar)]">AMVARMAR</span>
        </Link>

        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            className="grid size-10 place-items-center rounded-lg text-[var(--texto)] hover:bg-[var(--hover)]"
            onClick={alternarTema}
            aria-label={tema === "oscuro" ? "Usar tema claro" : "Usar tema oscuro"}
            title={tema === "oscuro" ? "Usar tema claro" : "Usar tema oscuro"}
          >
            {tema === "oscuro" ? <Sun className="size-5" /> : <Moon className="size-5" />}
          </button>

          <AmviChat />
          <CampanaNotificaciones />

          <div className="relative">
            <button
              type="button"
              className="flex h-11 items-center gap-2 rounded-lg px-2 text-left hover:bg-[var(--hover)]"
              onClick={() => setMenuUsuario((valor) => !valor)}
              aria-expanded={menuUsuario}
            >
              <span className="grid size-[34px] place-items-center rounded-full bg-[var(--marca-tenue)] text-xs font-bold text-[var(--marca)]">
                {iniciales(usuario.first_name, usuario.last_name)}
              </span>
              <span className="hidden max-w-40 md:block">
                <strong className="block truncate text-sm">
                  {usuario.first_name} {usuario.last_name}
                </strong>
                <span className="block truncate text-xs font-normal text-[var(--texto-secundario)]">
                  {usuario.email}
                </span>
              </span>
              <ChevronDown className="size-4 text-[var(--texto-secundario)]" aria-hidden="true" />
            </button>

            {menuUsuario ? (
              <div className="absolute right-0 mt-2 w-56 rounded-lg border bg-[var(--superficie)] p-1.5 shadow-xl">
                <Link
                  href="/cuenta"
                  className="flex items-center gap-2 rounded px-3 py-2 text-sm hover:bg-[var(--hover)]"
                  onClick={() => setMenuUsuario(false)}
                >
                  <UserRound className="size-4" aria-hidden="true" />
                  Mi cuenta
                </Link>
                <Link
                  href="/sesiones"
                  className="flex items-center gap-2 rounded px-3 py-2 text-sm hover:bg-[var(--hover)]"
                  onClick={() => setMenuUsuario(false)}
                >
                  <Settings className="size-4" aria-hidden="true" />
                  Sesiones activas
                </Link>
                <button
                  type="button"
                  className="flex w-full items-center gap-2 rounded px-3 py-2 text-sm text-[var(--peligro)] hover:bg-[var(--peligro-tenue)]"
                  onClick={() => void salir()}
                >
                  <LogOut className="size-4" aria-hidden="true" />
                  Cerrar sesión
                </button>
              </div>
            ) : null}
          </div>
        </div>
      </header>

      <aside
        className="fixed bottom-0 left-0 top-[var(--header-h)] z-30 hidden overflow-y-auto border-r bg-[var(--superficie)] transition-[width] duration-200 lg:block"
        style={{ width: expandido ? "var(--sb-w-expanded)" : "var(--sb-w)" }}
        aria-label="Navegación principal"
      >
        <nav className="space-y-1 p-3">{enlaces(expandido)}</nav>
      </aside>

      {menuMovil ? (
        <div
          className="fixed inset-0 z-50 bg-black/40 lg:hidden"
          onMouseDown={() => setMenuMovil(false)}
        >
          <aside
            className="flex h-full w-[min(86vw,var(--sb-w-expanded))] flex-col bg-[var(--superficie)]"
            onMouseDown={(evento) => evento.stopPropagation()}
            aria-label="Navegación principal"
          >
            <div className="flex h-[var(--header-h)] items-center justify-between border-b px-4">
              <span className="text-lg font-extrabold text-[var(--mar)]">AMVARMAR</span>
              <button
                type="button"
                className="grid size-9 place-items-center rounded-lg hover:bg-[var(--hover)]"
                onClick={() => setMenuMovil(false)}
                aria-label="Cerrar navegación"
              >
                <X className="size-5" />
              </button>
            </div>
            <nav className="flex-1 space-y-1 p-3">{enlaces(true)}</nav>
            <p className="truncate border-t px-4 py-3 text-xs text-[var(--texto-secundario)]">
              {usuario.empresa?.trade_name ?? usuario.empresa?.legal_name ?? "AMVARMAR Operaciones"}
            </p>
          </aside>
        </div>
      ) : null}

      <div
        className="pt-[var(--header-h)] transition-[padding] duration-200"
        style={{ paddingLeft: 0 }}
      >
        <div
          className="lg:[padding-left:var(--sb-actual)]"
          style={{ ["--sb-actual" as string]: expandido ? "var(--sb-w-expanded)" : "var(--sb-w)" }}
        >
          <main className="mx-auto w-full max-w-[1500px] px-4 py-6 sm:px-6 lg:px-8">{children}</main>
        </div>
      </div>
    </div>
  );
}
