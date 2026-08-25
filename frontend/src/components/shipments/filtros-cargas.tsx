"use client";

import { CalendarDays, ChevronDown, RotateCcw, Search } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Boton } from "@/components/ui/boton";
import { estadosCarga, etiquetaEstado } from "@/features/shipments/catalogo-estados";
import type { EstadoCarga } from "@/lib/api/tipos";

export interface FiltrosCarga {
  q: string;
  estados: EstadoCarga[];
  etaDesde: string;
  etaHasta: string;
}

export const filtrosIniciales: FiltrosCarga = { q: "", estados: [], etaDesde: "", etaHasta: "" };

export function FiltrosCargas({ aplicar }: { aplicar: (filtros: FiltrosCarga) => void }) {
  const [borrador, setBorrador] = useState<FiltrosCarga>(filtrosIniciales);

  function alternarEstado(estado: EstadoCarga) {
    setBorrador((actual) => ({
      ...actual,
      estados: actual.estados.includes(estado)
        ? actual.estados.filter((elemento) => elemento !== estado)
        : [...actual.estados, estado],
    }));
  }

  function enviar(evento: FormEvent) {
    evento.preventDefault();
    aplicar({ ...borrador, q: borrador.q.trim() });
  }

  function limpiar() {
    setBorrador(filtrosIniciales);
    aplicar(filtrosIniciales);
  }

  return (
    <form className="grid gap-3 border-y bg-white p-4 lg:grid-cols-[minmax(220px,1fr)_220px_165px_165px_auto]" onSubmit={enviar}>
      <label className="relative">
        <span className="sr-only">Buscar cargas</span>
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--texto-secundario)]" />
        <input
          className="h-10 w-full rounded-md border bg-white pl-9 pr-3 text-sm outline-none placeholder:text-[#8b969b] focus:border-[var(--mar)]"
          value={borrador.q}
          onChange={(evento) => setBorrador((actual) => ({ ...actual, q: evento.target.value }))}
          placeholder="Número, factura o referencia"
        />
      </label>

      <details className="group relative">
        <summary className="flex h-10 cursor-pointer list-none items-center justify-between rounded-md border bg-white px-3 text-sm [&::-webkit-details-marker]:hidden">
          <span>{borrador.estados.length ? `${borrador.estados.length} estados` : "Todos los estados"}</span>
          <ChevronDown className="size-4 transition-transform group-open:rotate-180" aria-hidden="true" />
        </summary>
        <div className="absolute left-0 top-12 z-10 grid max-h-80 w-64 gap-1 overflow-y-auto rounded-md border bg-white p-2 shadow-xl">
          {estadosCarga.map((estado) => (
            <label key={estado} className="flex cursor-pointer items-center gap-2 rounded px-2 py-2 text-sm hover:bg-[#edf1f2]">
              <input
                type="checkbox"
                className="size-4 accent-[var(--mar)]"
                checked={borrador.estados.includes(estado)}
                onChange={() => alternarEstado(estado)}
              />
              {etiquetaEstado[estado]}
            </label>
          ))}
        </div>
      </details>

      <label className="relative">
        <span className="sr-only">ETA desde</span>
        <CalendarDays className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--texto-secundario)]" />
        <input
          type="date"
          className="h-10 w-full rounded-md border bg-white pl-9 pr-2 text-sm outline-none focus:border-[var(--mar)]"
          value={borrador.etaDesde}
          onChange={(evento) => setBorrador((actual) => ({ ...actual, etaDesde: evento.target.value }))}
          aria-label="ETA desde"
        />
      </label>

      <label className="relative">
        <span className="sr-only">ETA hasta</span>
        <CalendarDays className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--texto-secundario)]" />
        <input
          type="date"
          className="h-10 w-full rounded-md border bg-white pl-9 pr-2 text-sm outline-none focus:border-[var(--mar)]"
          value={borrador.etaHasta}
          onChange={(evento) => setBorrador((actual) => ({ ...actual, etaHasta: evento.target.value }))}
          aria-label="ETA hasta"
        />
      </label>

      <div className="flex gap-2">
        <Boton type="submit" className="flex-1 lg:flex-none">Aplicar</Boton>
        <button
          type="button"
          className="grid size-10 shrink-0 place-items-center rounded-md border bg-white text-[var(--texto-secundario)] hover:bg-[#edf1f2]"
          onClick={limpiar}
          title="Limpiar filtros"
          aria-label="Limpiar filtros"
        >
          <RotateCcw className="size-4" />
        </button>
      </div>
    </form>
  );
}
