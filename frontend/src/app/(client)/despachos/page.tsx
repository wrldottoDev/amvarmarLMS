"use client";

import { PackageCheck, Plus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { InsigniaDespacho } from "@/components/despachos/insignia-despacho";
import { AvisoError } from "@/components/ui/aviso-error";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import { useSesion } from "@/features/auth/contexto-sesion";
import { etiquetaDespacho, type EstadoDespacho } from "@/features/despachos/catalogo";
import { useDespachos } from "@/features/despachos/consultas";
import { clases, formatearFecha } from "@/lib/utilidades";

const FILTROS: { valor: EstadoDespacho | undefined; etiqueta: string }[] = [
  { valor: undefined, etiqueta: "Todos" },
  { valor: "PENDING", etiqueta: "Esperando aprobación" },
  { valor: "PREPARING", etiqueta: "Preparando" },
  { valor: "COMPLETED", etiqueta: "Despachados" },
];

export default function PaginaDespachos() {
  const [estado, setEstado] = useState<EstadoDespacho | undefined>(undefined);
  const { usuario } = useSesion();
  const { data, isPending, error } = useDespachos({ estado });

  const esCliente = Boolean(usuario?.empresa);

  return (
    <section className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">Despachos</h1>
          <p className="mt-0.5 text-sm text-[var(--texto-secundario)]">
            {esCliente
              ? "Tus solicitudes para sacar carga de bodega."
              : "Solicitudes de despacho de todas las empresas."}
          </p>
        </div>

        {esCliente ? (
          <Link
            href="/despachos/nuevo"
            className="flex h-10 items-center gap-2 rounded-md bg-[var(--mar)] px-4 text-sm font-semibold text-white hover:opacity-90"
          >
            <Plus className="size-4" aria-hidden="true" />
            Solicitar despacho
          </Link>
        ) : null}
      </header>

      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filtrar por estado">
        {FILTROS.map((filtro) => (
          <button
            key={filtro.etiqueta}
            type="button"
            onClick={() => setEstado(filtro.valor)}
            className={clases(
              "rounded-full border px-3.5 py-1.5 text-sm font-medium",
              estado === filtro.valor
                ? "border-[var(--mar)] bg-[var(--mar)] text-white"
                : "border-[#d6dfe2] bg-white text-[var(--texto-secundario)] hover:bg-[#edf1f2]",
            )}
          >
            {filtro.etiqueta}
          </button>
        ))}
      </div>

      {error ? <AvisoError error={error} /> : null}
      {isPending ? <CargandoPagina /> : null}

      {data && data.items.length > 0 ? (
        <ul className="grid gap-3">
          {data.items.map((despacho) => (
            <li key={despacho.id}>
              <Link
                href={`/despachos/${despacho.id}`}
                className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-white px-4 py-4 hover:border-[var(--mar)]"
              >
                <div className="min-w-0">
                  <strong className="block text-sm">{despacho.dispatch_number}</strong>
                  <span className="mt-0.5 block text-sm text-[var(--texto-secundario)]">
                    {despacho.shipment_count === 1
                      ? "1 carga"
                      : `${despacho.shipment_count} cargas`}{" "}
                    · Solicitado el {formatearFecha(despacho.requested_at)}
                  </span>
                </div>
                <InsigniaDespacho estado={despacho.status} />
              </Link>
            </li>
          ))}
        </ul>
      ) : null}

      {data && data.items.length === 0 ? (
        <div className="rounded-md border bg-white px-6 py-16 text-center">
          <PackageCheck
            className="mx-auto size-8 text-[var(--texto-secundario)]"
            aria-hidden="true"
          />
          <p className="mt-3 text-sm font-medium">
            {estado
              ? `No hay despachos en "${etiquetaDespacho[estado] ?? estado}".`
              : "Todavía no hay despachos."}
          </p>
          {esCliente && !estado ? (
            <>
              <p className="mt-1 text-sm text-[var(--texto-secundario)]">
                Cuando tengas carga almacenada, pedí el despacho desde acá.
              </p>
              <Link
                href="/despachos/nuevo"
                className="mt-4 inline-flex h-10 items-center gap-2 rounded-md bg-[var(--mar)] px-4 text-sm font-semibold text-white hover:opacity-90"
              >
                <Plus className="size-4" aria-hidden="true" />
                Solicitar despacho
              </Link>
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
