"use client";

import { Plus, Trash2 } from "lucide-react";
import { clases } from "@/lib/utilidades";

/** Los mismos nombres que usaba el desplegable del sistema viejo. */
export const TIPOS_DE_PIEZA = [
  { valor: "PALLET", etiqueta: "Pallets" },
  { valor: "BOX", etiqueta: "Cajas" },
  { valor: "DRUM", etiqueta: "Tambores" },
  { valor: "BUNDLE", etiqueta: "Bultos" },
  { valor: "OTHER", etiqueta: "Otro" },
] as const;

export interface Pieza {
  /** Clave local de la fila. No es el id del servidor. */
  clave: string;
  package_type: string;
  quantity: string;
  description: string;
  weight_kg: string;
  length_cm: string;
  width_cm: string;
  height_cm: string;
}

export function piezaVacia(): Pieza {
  return {
    clave: crypto.randomUUID(),
    package_type: "PALLET",
    quantity: "1",
    description: "",
    weight_kg: "",
    length_cm: "",
    width_cm: "",
    height_cm: "",
  };
}

/** El total de unidades, que es lo que cuenta el sistema, no la cantidad de filas. */
export function totalUnidades(piezas: Pieza[]): number {
  return piezas.reduce((suma, p) => suma + (Number(p.quantity) || 0), 0);
}

/**
 * Lo que se manda al API. Los campos vacíos van como `null` y no como cero:
 * un bulto de cero kilos es un dato mal capturado, y el backend lo rechaza.
 */
export function aPayload(piezas: Pieza[]) {
  const numero = (texto: string) => (texto.trim() === "" ? null : texto.trim());
  return piezas.map((p) => ({
    package_type: p.package_type,
    quantity: Number(p.quantity),
    description: p.description.trim() || null,
    weight_kg: numero(p.weight_kg),
    length_cm: numero(p.length_cm),
    width_cm: numero(p.width_cm),
    height_cm: numero(p.height_cm),
  }));
}

/** Qué está mal en el desglose, o `null` si se puede guardar. */
export function problemaDePiezas(piezas: Pieza[]): string | null {
  if (piezas.length === 0) return "Agregá al menos una pieza.";

  for (const [indice, pieza] of piezas.entries()) {
    const cantidad = Number(pieza.quantity);
    if (!Number.isInteger(cantidad) || cantidad < 1) {
      return `La pieza ${indice + 1} necesita una cantidad de al menos 1.`;
    }
    for (const [etiqueta, valor] of [
      ["peso", pieza.weight_kg],
      ["largo", pieza.length_cm],
      ["ancho", pieza.width_cm],
      ["alto", pieza.height_cm],
    ] as const) {
      if (valor.trim() !== "" && Number(valor) <= 0) {
        return `El ${etiqueta} de la pieza ${indice + 1} debe ser mayor que cero.`;
      }
    }
  }
  return null;
}

/**
 * El desglose de bultos de una carga.
 *
 * Compartido por el alta y la edición: mantener dos formularios distintos hace
 * que las reglas se separen y una de las dos pantallas termine aceptando lo que
 * la otra rechaza.
 *
 * Nace con una fila porque toda carga necesita al menos una pieza, y el botón
 * de quitar se deshabilita cuando queda una sola: dejar que la interfaz llegue
 * a cero para que el servidor lo rechace después es hacerle perder el trabajo a
 * quien lo escribió.
 */
export function EditorPiezas({
  piezas,
  alCambiar,
  deshabilitado = false,
}: {
  piezas: Pieza[];
  alCambiar: (piezas: Pieza[]) => void;
  deshabilitado?: boolean;
}) {
  const total = totalUnidades(piezas);
  const problema = problemaDePiezas(piezas);

  function cambiar(clave: string, campo: keyof Pieza, valor: string) {
    alCambiar(piezas.map((p) => (p.clave === clave ? { ...p, [campo]: valor } : p)));
  }

  return (
    <div className="rounded-lg border bg-[var(--superficie)]">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2.5">
        <span className="font-semibold">
          Tipos de carga (piezas)
          <span className="ml-2 font-normal text-[var(--texto-secundario)]">
            {total} {total === 1 ? "unidad" : "unidades"}
          </span>
        </span>
        <button
          type="button"
          className="flex h-9 items-center gap-1.5 rounded-md border border-[var(--marca)] px-3 text-sm font-medium text-[var(--marca)] hover:bg-[var(--marca-tenue)] disabled:opacity-50"
          onClick={() => alCambiar([...piezas, piezaVacia()])}
          disabled={deshabilitado || piezas.length >= 50}
        >
          <Plus className="size-4" aria-hidden="true" />
          Agregar pieza
        </button>
      </div>

      <div className="space-y-3 p-4">
        {piezas.map((pieza, indice) => (
          <div key={pieza.clave} className="grid gap-3 rounded-lg border p-3 sm:grid-cols-12">
            <label className="block sm:col-span-3">
              <span className="mb-1 block text-sm font-medium">Tipo</span>
              <select
                className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
                value={pieza.package_type}
                disabled={deshabilitado}
                onChange={(evento) => cambiar(pieza.clave, "package_type", evento.target.value)}
              >
                {TIPOS_DE_PIEZA.map((t) => (
                  <option key={t.valor} value={t.valor}>
                    {t.etiqueta}
                  </option>
                ))}
              </select>
            </label>

            <label className="block sm:col-span-2">
              <span className="mb-1 block text-sm font-medium">Cantidad</span>
              <input
                type="number"
                min="1"
                step="1"
                className={clases(
                  "w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm",
                  Number(pieza.quantity) < 1 && "border-[var(--peligro)]",
                )}
                value={pieza.quantity}
                disabled={deshabilitado}
                onChange={(evento) => cambiar(pieza.clave, "quantity", evento.target.value)}
              />
            </label>

            <label className="block sm:col-span-4">
              <span className="mb-1 block text-sm font-medium">Descripción</span>
              <input
                className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
                value={pieza.description}
                disabled={deshabilitado}
                onChange={(evento) => cambiar(pieza.clave, "description", evento.target.value)}
              />
            </label>

            <label className="block sm:col-span-2">
              <span className="mb-1 block text-sm font-medium">Peso (kg)</span>
              <input
                type="number"
                min="0"
                step="0.001"
                className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
                value={pieza.weight_kg}
                disabled={deshabilitado}
                onChange={(evento) => cambiar(pieza.clave, "weight_kg", evento.target.value)}
              />
            </label>

            <div className="flex items-end sm:col-span-1">
              <button
                type="button"
                className="grid h-10 w-full place-items-center rounded-md border text-[var(--peligro)] hover:bg-[var(--peligro-tenue)] disabled:opacity-40"
                onClick={() => alCambiar(piezas.filter((p) => p.clave !== pieza.clave))}
                // La última no se quita: toda carga necesita al menos una pieza.
                disabled={deshabilitado || piezas.length === 1}
                aria-label={`Quitar la pieza ${indice + 1}`}
                title={
                  piezas.length === 1
                    ? "Toda carga necesita al menos una pieza"
                    : "Quitar esta pieza"
                }
              >
                <Trash2 className="size-4" aria-hidden="true" />
              </button>
            </div>

            <div className="grid gap-3 sm:col-span-12 sm:grid-cols-3">
              {(
                [
                  ["length_cm", "Largo (cm)"],
                  ["width_cm", "Ancho (cm)"],
                  ["height_cm", "Alto (cm)"],
                ] as const
              ).map(([campo, etiqueta]) => (
                <label className="block" key={campo}>
                  <span className="mb-1 block text-xs text-[var(--texto-secundario)]">
                    {etiqueta}
                  </span>
                  <input
                    type="number"
                    min="0"
                    step="0.1"
                    className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
                    value={pieza[campo]}
                    disabled={deshabilitado}
                    onChange={(evento) => cambiar(pieza.clave, campo, evento.target.value)}
                  />
                </label>
              ))}
            </div>
          </div>
        ))}

        {problema ? (
          <p className="text-sm text-[var(--peligro)]" role="alert">
            {problema}
          </p>
        ) : null}
      </div>
    </div>
  );
}
