"use client";

import { CalendarDays, ChevronDown, Filter, RotateCcw, Search, X } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { SelectorEmpresa } from "@/components/admin/selector-empresa";
import { Boton } from "@/components/ui/boton";
import { estadosCarga, etiquetaEstado } from "@/features/shipments/catalogo-estados";
import type { components } from "@/lib/api/generated";
import type { EstadoCarga } from "@/lib/api/tipos";

type TipoReferencia = components["schemas"]["ReferenceType"];

export interface FiltrosCarga {
  q: string;
  shipmentNumber: string;
  wr: string;
  shipper: string;
  carrier: string;
  reference: string;
  referenceType: TipoReferencia | "";
  companyId: string;
  estados: EstadoCarga[];
  etaDesde: string;
  etaHasta: string;
}

export const filtrosIniciales: FiltrosCarga = {
  q: "",
  shipmentNumber: "",
  wr: "",
  shipper: "",
  carrier: "",
  reference: "",
  referenceType: "",
  companyId: "",
  estados: [],
  etaDesde: "",
  etaHasta: "",
};

const referencias: { valor: TipoReferencia; etiqueta: string }[] = [
  { valor: "INVOICE", etiqueta: "Factura" },
  { valor: "WR", etiqueta: "WR" },
  { valor: "PO", etiqueta: "Orden de compra" },
  { valor: "TRACKING", etiqueta: "Tracking" },
  { valor: "CONTAINER", etiqueta: "Contenedor" },
  { valor: "BL", etiqueta: "BL" },
  { valor: "OTHER", etiqueta: "Otra referencia" },
];

export function filtrosDesdeParametros(parametros: URLSearchParams): FiltrosCarga {
  const estados = parametros
    .getAll("status")
    .filter((estado): estado is EstadoCarga => estadosCarga.includes(estado as EstadoCarga));
  const tipo = parametros.get("reference_type") ?? "";
  return {
    q: parametros.get("q") ?? "",
    shipmentNumber: parametros.get("shipment_number") ?? "",
    wr: parametros.get("wr") ?? "",
    shipper: parametros.get("shipper") ?? "",
    carrier: parametros.get("carrier") ?? "",
    reference: parametros.get("reference") ?? "",
    referenceType: referencias.some((item) => item.valor === tipo)
      ? (tipo as TipoReferencia)
      : "",
    companyId: parametros.get("company_id") ?? "",
    estados,
    etaDesde: parametros.get("eta_from") ?? "",
    etaHasta: parametros.get("eta_to") ?? "",
  };
}

export function parametrosDeFiltros(filtros: FiltrosCarga): URLSearchParams {
  const parametros = new URLSearchParams();
  const valores: [string, string][] = [
    ["q", filtros.q.trim()],
    ["shipment_number", filtros.shipmentNumber.trim()],
    ["wr", filtros.wr.trim()],
    ["shipper", filtros.shipper.trim()],
    ["carrier", filtros.carrier.trim()],
    ["reference", filtros.reference.trim()],
    ["reference_type", filtros.referenceType],
    ["company_id", filtros.companyId],
    ["eta_from", filtros.etaDesde],
    ["eta_to", filtros.etaHasta],
  ];
  for (const [clave, valor] of valores) if (valor) parametros.set(clave, valor);
  for (const estado of filtros.estados) parametros.append("status", estado);
  return parametros;
}

function textoLimpio(filtros: FiltrosCarga): FiltrosCarga {
  return {
    ...filtros,
    q: filtros.q.trim(),
    shipmentNumber: filtros.shipmentNumber.trim(),
    wr: filtros.wr.trim(),
    shipper: filtros.shipper.trim(),
    carrier: filtros.carrier.trim(),
    reference: filtros.reference.trim(),
  };
}

export function FiltrosCargas({
  valor,
  aplicar,
  mostrarEmpresa,
}: {
  valor: FiltrosCarga;
  aplicar: (filtros: FiltrosCarga) => void;
  mostrarEmpresa: boolean;
}) {
  const [borrador, setBorrador] = useState<FiltrosCarga>(valor);
  const ultimoQ = useRef(valor.q);

  useEffect(() => {
    const q = borrador.q.trim();
    if (q === ultimoQ.current) return;
    const temporizador = window.setTimeout(() => {
      ultimoQ.current = q;
      aplicar({ ...valor, q });
    }, 350);
    return () => window.clearTimeout(temporizador);
  }, [aplicar, borrador.q, valor]);

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
    const limpios = textoLimpio(borrador);
    ultimoQ.current = limpios.q;
    aplicar(limpios);
  }

  function limpiar() {
    setBorrador(filtrosIniciales);
    ultimoQ.current = "";
    aplicar(filtrosIniciales);
  }

  const chips: { clave: keyof FiltrosCarga; etiqueta: string }[] = [
    ...(valor.shipmentNumber
      ? [{ clave: "shipmentNumber" as const, etiqueta: `SHP: ${valor.shipmentNumber}` }]
      : []),
    ...(valor.wr ? [{ clave: "wr" as const, etiqueta: `WR: ${valor.wr}` }] : []),
    ...(valor.shipper
      ? [{ clave: "shipper" as const, etiqueta: `Shipper: ${valor.shipper}` }]
      : []),
    ...(valor.carrier
      ? [{ clave: "carrier" as const, etiqueta: `Carrier: ${valor.carrier}` }]
      : []),
    ...(valor.reference
      ? [{ clave: "reference" as const, etiqueta: `Referencia: ${valor.reference}` }]
      : []),
    ...(valor.referenceType
      ? [
          {
            clave: "referenceType" as const,
            etiqueta:
              referencias.find((item) => item.valor === valor.referenceType)?.etiqueta ??
              valor.referenceType,
          },
        ]
      : []),
    ...(valor.companyId
      ? [{ clave: "companyId" as const, etiqueta: "Empresa seleccionada" }]
      : []),
    ...(valor.etaDesde
      ? [{ clave: "etaDesde" as const, etiqueta: `ETA desde: ${valor.etaDesde}` }]
      : []),
    ...(valor.etaHasta
      ? [{ clave: "etaHasta" as const, etiqueta: `ETA hasta: ${valor.etaHasta}` }]
      : []),
  ];

  return (
    <form className="border-y bg-[var(--superficie)]" onSubmit={enviar}>
      <div className="grid gap-3 p-4 md:grid-cols-[minmax(240px,1fr)_auto_auto]">
        <label className="relative">
          <span className="sr-only">Buscar cargas</span>
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--texto-secundario)]" />
          <input
            className="h-10 w-full rounded-md border bg-[var(--superficie)] pl-9 pr-3 text-sm outline-none placeholder:text-[var(--texto-secundario)] focus:border-[var(--mar)]"
            value={borrador.q}
            onChange={(evento) =>
              setBorrador((actual) => ({ ...actual, q: evento.target.value }))
            }
            placeholder="SHP, WR, shipper, carrier o referencia"
          />
        </label>

        <details className="group relative">
          <summary className="flex h-10 cursor-pointer list-none items-center justify-between gap-2 rounded-md border px-3 text-sm font-medium [&::-webkit-details-marker]:hidden">
            <Filter className="size-4" aria-hidden="true" />
            Filtros
            <ChevronDown className="size-4 transition-transform group-open:rotate-180" />
          </summary>
          <div className="absolute right-0 top-12 z-20 w-[min(92vw,720px)] border bg-[var(--superficie)] p-4 shadow-xl">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {(
                [
                  ["shipmentNumber", "Número SHP", "SHP-000123"],
                  ["wr", "Número WR", "WR105921"],
                  ["shipper", "Shipper", "Remitente"],
                  ["carrier", "Carrier", "Transportista"],
                  ["reference", "Referencia", "Factura, PO, tracking…"],
                ] as const
              ).map(([campo, etiqueta, placeholder]) => (
                <label key={campo} className="block">
                  <span className="mb-1 block text-xs font-medium">{etiqueta}</span>
                  <input
                    className="h-10 w-full rounded-md border px-3 text-sm"
                    value={borrador[campo]}
                    placeholder={placeholder}
                    onChange={(evento) =>
                      setBorrador((actual) => ({
                        ...actual,
                        [campo]: evento.target.value,
                      }))
                    }
                  />
                </label>
              ))}

              <label className="block">
                <span className="mb-1 block text-xs font-medium">Tipo de referencia</span>
                <select
                  className="h-10 w-full rounded-md border px-3 text-sm"
                  value={borrador.referenceType}
                  onChange={(evento) =>
                    setBorrador((actual) => ({
                      ...actual,
                      referenceType: evento.target.value as TipoReferencia | "",
                    }))
                  }
                >
                  <option value="">Cualquier tipo</option>
                  {referencias.map((tipo) => (
                    <option key={tipo.valor} value={tipo.valor}>
                      {tipo.etiqueta}
                    </option>
                  ))}
                </select>
              </label>

              {mostrarEmpresa ? (
                <label className="block sm:col-span-2 lg:col-span-1">
                  <span className="mb-1 block text-xs font-medium">Empresa</span>
                  <SelectorEmpresa
                    valor={borrador.companyId}
                    alCambiar={(companyId) =>
                      setBorrador((actual) => ({ ...actual, companyId }))
                    }
                  />
                </label>
              ) : null}

              <label className="relative block">
                <span className="mb-1 block text-xs font-medium">ETA desde</span>
                <CalendarDays className="pointer-events-none absolute bottom-3 left-3 size-4 text-[var(--texto-secundario)]" />
                <input
                  type="date"
                  className="h-10 w-full rounded-md border pl-9 pr-2 text-sm"
                  value={borrador.etaDesde}
                  onChange={(evento) =>
                    setBorrador((actual) => ({ ...actual, etaDesde: evento.target.value }))
                  }
                />
              </label>

              <label className="relative block">
                <span className="mb-1 block text-xs font-medium">ETA hasta</span>
                <CalendarDays className="pointer-events-none absolute bottom-3 left-3 size-4 text-[var(--texto-secundario)]" />
                <input
                  type="date"
                  className="h-10 w-full rounded-md border pl-9 pr-2 text-sm"
                  value={borrador.etaHasta}
                  onChange={(evento) =>
                    setBorrador((actual) => ({ ...actual, etaHasta: evento.target.value }))
                  }
                />
              </label>
            </div>

            <fieldset className="mt-4 border-t pt-3">
              <legend className="px-1 text-xs font-medium">Estados</legend>
              <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
                {estadosCarga.map((estado) => (
                  <label key={estado} className="flex cursor-pointer items-center gap-2 px-2 py-1.5 text-sm hover:bg-[var(--hover)]">
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
            </fieldset>
          </div>
        </details>

        <div className="flex gap-2">
          <Boton type="submit">Aplicar</Boton>
          <button
            type="button"
            className="grid size-10 place-items-center rounded-md border text-[var(--texto-secundario)] hover:bg-[var(--hover)]"
            onClick={limpiar}
            title="Limpiar filtros"
            aria-label="Limpiar filtros"
          >
            <RotateCcw className="size-4" />
          </button>
        </div>
      </div>

      {chips.length || valor.estados.length || valor.etaDesde || valor.etaHasta ? (
        <div className="flex flex-wrap gap-2 border-t px-4 py-2.5" aria-label="Filtros activos">
          {chips.map((chip) => (
            <button
              key={chip.clave}
              type="button"
              className="flex items-center gap-1 rounded-full bg-[var(--hover)] px-2.5 py-1 text-xs"
              onClick={() => aplicar({ ...valor, [chip.clave]: "" })}
            >
              {chip.etiqueta}
              <X className="size-3" aria-hidden="true" />
            </button>
          ))}
          {valor.estados.map((estado) => (
            <button
              key={estado}
              type="button"
              className="flex items-center gap-1 rounded-full bg-[var(--hover)] px-2.5 py-1 text-xs"
              onClick={() =>
                aplicar({ ...valor, estados: valor.estados.filter((item) => item !== estado) })
              }
            >
              {etiquetaEstado[estado]}
              <X className="size-3" aria-hidden="true" />
            </button>
          ))}
        </div>
      ) : null}
    </form>
  );
}
