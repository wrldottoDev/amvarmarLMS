"use client";

import { BellOff, Check } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import {
  useMarcarLeida,
  useMarcarTodasLeidas,
  useNotificaciones,
} from "@/features/notificaciones/consultas";
import { rutaDeRecurso } from "@/features/notificaciones/rutas";
import { AvisoError } from "@/components/ui/aviso-error";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import { clases, formatearFechaHora, tiempoRelativo } from "@/lib/utilidades";

export default function PaginaAvisos() {
  const [soloNoLeidas, setSoloNoLeidas] = useState(false);
  const { data, isPending, error } = useNotificaciones({ soloNoLeidas, limite: 50 });
  const marcarLeida = useMarcarLeida();
  const marcarTodas = useMarcarTodasLeidas();

  if (isPending) return <CargandoPagina />;
  if (error) return <AvisoError error={error} />;

  const sinLeer = data?.unread_count ?? 0;

  return (
    <section className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">Avisos</h1>
          <p className="mt-0.5 text-sm text-[var(--texto-secundario)]">
            {sinLeer > 0
              ? `Tenés ${sinLeer} sin leer.`
              : "Estás al día. No hay nada pendiente de leer."}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex rounded-md border p-0.5" role="group" aria-label="Filtrar avisos">
            {[
              { valor: false, etiqueta: "Todos" },
              { valor: true, etiqueta: "Sin leer" },
            ].map((opcion) => (
              <button
                key={opcion.etiqueta}
                type="button"
                onClick={() => setSoloNoLeidas(opcion.valor)}
                className={clases(
                  "rounded px-3 py-1.5 text-sm font-medium",
                  soloNoLeidas === opcion.valor
                    ? "bg-[var(--mar)] text-white"
                    : "text-[var(--texto-secundario)] hover:bg-[var(--hover)]",
                )}
              >
                {opcion.etiqueta}
              </button>
            ))}
          </div>

          {sinLeer > 0 ? (
            <button
              type="button"
              className="flex h-9 items-center gap-1.5 rounded-md border px-3 text-sm font-medium hover:bg-[var(--hover)]"
              onClick={() => marcarTodas.mutate()}
              disabled={marcarTodas.isPending}
            >
              <Check className="size-4" aria-hidden="true" />
              Marcar todo como leído
            </button>
          ) : null}
        </div>
      </header>

      {data && data.items.length > 0 ? (
        <ul className="divide-y overflow-hidden rounded-md border bg-[var(--superficie)]">
          {data.items.map((aviso) => (
            <li key={aviso.id}>
              <Link
                href={rutaDeRecurso(aviso)}
                className={clases(
                  "flex items-start gap-3 px-4 py-4 hover:bg-[var(--hover)]",
                  aviso.read_at ? "" : "bg-[var(--marca-tenue)]",
                )}
                onClick={() => {
                  if (!aviso.read_at) marcarLeida.mutate(aviso.id);
                }}
              >
                <span
                  className={clases(
                    "mt-1.5 size-2 shrink-0 rounded-full",
                    aviso.read_at ? "bg-transparent" : "bg-[var(--mar)]",
                  )}
                  aria-hidden="true"
                />
                <span className="min-w-0 flex-1">
                  <strong className="block text-sm">{aviso.title}</strong>
                  <span className="mt-0.5 block text-sm text-[var(--texto-secundario)]">
                    {aviso.body}
                  </span>
                </span>
                <time
                  className="shrink-0 text-xs text-[var(--texto-secundario)]"
                  dateTime={aviso.created_at}
                  title={formatearFechaHora(aviso.created_at)}
                >
                  {tiempoRelativo(aviso.created_at)}
                </time>
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <div className="rounded-md border bg-[var(--superficie)] px-6 py-16 text-center">
          <BellOff className="mx-auto size-8 text-[var(--texto-secundario)]" aria-hidden="true" />
          <p className="mt-3 text-sm font-medium">
            {soloNoLeidas ? "No tenés avisos sin leer." : "Todavía no tenés avisos."}
          </p>
          <p className="mt-1 text-sm text-[var(--texto-secundario)]">
            Te avisamos acá cuando algo pase con tus cargas.
          </p>
        </div>
      )}
    </section>
  );
}
