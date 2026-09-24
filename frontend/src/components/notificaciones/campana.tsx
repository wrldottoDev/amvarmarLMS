"use client";

import { Bell, Check } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  useMarcarLeida,
  useMarcarTodasLeidas,
  useNotificaciones,
} from "@/features/notificaciones/consultas";
import { rutaDeRecurso } from "@/features/notificaciones/rutas";
import { clases, tiempoRelativo } from "@/lib/utilidades";

export function CampanaNotificaciones() {
  const [abierto, setAbierto] = useState(false);
  const contenedor = useRef<HTMLDivElement>(null);
  const { data } = useNotificaciones({ limite: 8 });
  const marcarLeida = useMarcarLeida();
  const marcarTodas = useMarcarTodasLeidas();

  const sinLeer = data?.unread_count ?? 0;

  // Cerrar al tocar fuera. En móvil el panel ocupa casi toda la pantalla y sin
  // esto hay que buscar el botón de nuevo para salir.
  useEffect(() => {
    if (!abierto) return;
    function alTocarFuera(evento: MouseEvent) {
      if (!contenedor.current?.contains(evento.target as Node)) setAbierto(false);
    }
    document.addEventListener("mousedown", alTocarFuera);
    return () => document.removeEventListener("mousedown", alTocarFuera);
  }, [abierto]);

  return (
    <div className="relative" ref={contenedor}>
      <button
        type="button"
        className="relative grid size-10 place-items-center rounded-md text-[var(--texto-secundario)] hover:bg-[var(--hover)]"
        onClick={() => setAbierto((valor) => !valor)}
        aria-label={sinLeer > 0 ? `Avisos: ${sinLeer} sin leer` : "Avisos"}
        aria-expanded={abierto}
      >
        <Bell className="size-5" aria-hidden="true" />
        {sinLeer > 0 ? (
          <span className="absolute right-1 top-1 grid min-w-4 place-items-center rounded-full bg-[var(--peligro)] px-1 text-[10px] font-bold leading-4 text-white">
            {sinLeer > 9 ? "9+" : sinLeer}
          </span>
        ) : null}
      </button>

      {abierto ? (
        <div className="absolute right-0 z-30 mt-2 w-[min(92vw,380px)] rounded-md border bg-[var(--superficie)] shadow-xl">
          <div className="flex items-center justify-between border-b px-4 py-3">
            <strong className="text-sm">Avisos</strong>
            {sinLeer > 0 ? (
              <button
                type="button"
                className="flex items-center gap-1 text-xs font-medium text-[var(--mar)] hover:underline"
                onClick={() => marcarTodas.mutate()}
                disabled={marcarTodas.isPending}
              >
                <Check className="size-3.5" aria-hidden="true" />
                Marcar todo como leído
              </button>
            ) : null}
          </div>

          {data && data.items.length > 0 ? (
            <ul className="max-h-[60vh] divide-y overflow-y-auto">
              {data.items.map((aviso) => (
                <li key={aviso.id}>
                  <Link
                    href={rutaDeRecurso(aviso)}
                    className={clases(
                      "block px-4 py-3 hover:bg-[var(--hover)]",
                      aviso.read_at ? "" : "bg-[var(--marca-tenue)]",
                    )}
                    onClick={() => {
                      if (!aviso.read_at) marcarLeida.mutate(aviso.id);
                      setAbierto(false);
                    }}
                  >
                    <span className="flex items-start gap-2">
                      {aviso.read_at ? null : (
                        <span
                          className="mt-1.5 size-2 shrink-0 rounded-full bg-[var(--mar)]"
                          aria-hidden="true"
                        />
                      )}
                      <span className={aviso.read_at ? "pl-4" : ""}>
                        <strong className="block text-sm">{aviso.title}</strong>
                        <span className="mt-0.5 block text-xs text-[var(--texto-secundario)]">
                          {aviso.body}
                        </span>
                        <span className="mt-1 block text-[11px] text-[var(--texto-secundario)]">
                          {tiempoRelativo(aviso.created_at)}
                        </span>
                      </span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-4 py-8 text-center text-sm text-[var(--texto-secundario)]">
              No tenés avisos.
            </p>
          )}

          <div className="border-t px-4 py-2.5">
            <Link
              href="/avisos"
              className="block text-center text-xs font-medium text-[var(--mar)] hover:underline"
              onClick={() => setAbierto(false)}
            >
              Ver todos
            </Link>
          </div>
        </div>
      ) : null}
    </div>
  );
}
