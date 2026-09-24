"use client";

import { Scale } from "lucide-react";
import { clases } from "@/lib/utilidades";

export type UnidadPeso = "KG" | "LB";

export interface PesoEditable {
  kg: string;
  lb: string;
  unidadFuente: UnidadPeso;
}

const LIBRAS_POR_KILO = 2.2046226218;

export const pesoVacio: PesoEditable = { kg: "", lb: "", unidadFuente: "KG" };

function numero(valor: string): number | null {
  if (!valor.trim()) return null;
  const convertido = Number(valor.replace(",", "."));
  return Number.isFinite(convertido) && convertido > 0 ? convertido : null;
}

function tresDecimales(valor: number): string {
  return valor.toFixed(3);
}

export function cambiarPeso(
  actual: PesoEditable,
  unidad: UnidadPeso,
  texto: string,
): PesoEditable {
  const valor = numero(texto);
  if (unidad === "KG") {
    return {
      kg: texto,
      lb: valor === null ? "" : tresDecimales(valor * LIBRAS_POR_KILO),
      unidadFuente: "KG",
    };
  }
  return {
    kg: valor === null ? "" : tresDecimales(valor / LIBRAS_POR_KILO),
    lb: texto,
    unidadFuente: "LB",
  };
}

export function normalizarPeso(actual: PesoEditable): PesoEditable {
  const fuente = actual.unidadFuente === "KG" ? actual.kg : actual.lb;
  const valor = numero(fuente);
  if (valor === null) return actual;
  return cambiarPeso(actual, actual.unidadFuente, tresDecimales(valor));
}

export function pesoValido(peso: PesoEditable): boolean {
  return numero(peso.unidadFuente === "KG" ? peso.kg : peso.lb) !== null;
}

export function pesoParaApi(peso: PesoEditable): { value: string; unit: UnidadPeso } | null {
  const valor = peso.unidadFuente === "KG" ? peso.kg : peso.lb;
  if (numero(valor) === null) return null;
  return { value: valor.replace(",", "."), unit: peso.unidadFuente };
}

export function EditorPeso({
  valor,
  alCambiar,
  mostrarError = false,
}: {
  valor: PesoEditable;
  alCambiar: (valor: PesoEditable) => void;
  mostrarError?: boolean;
}) {
  const invalido = mostrarError && !pesoValido(valor);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Scale className="size-4 text-[var(--marca)]" aria-hidden="true" />
        <p className="text-xs text-[var(--texto-secundario)]">
          Editá cualquiera de las dos unidades; la otra se calcula automáticamente.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {(
          [
            { unidad: "KG", etiqueta: "Peso en kg", valor: valor.kg },
            { unidad: "LB", etiqueta: "Peso en libras", valor: valor.lb },
          ] as const
        ).map((campo) => {
          const esFuente = valor.unidadFuente === campo.unidad;
          return (
            <label className="block" key={campo.unidad}>
              <span className="mb-1 flex items-center justify-between gap-2 text-sm font-medium">
                {campo.etiqueta}
                <span className="text-[11px] font-normal text-[var(--texto-secundario)]">
                  {esFuente ? "Fuente" : "Calculado"}
                </span>
              </span>
              <input
                type="number"
                inputMode="decimal"
                min="0.001"
                step="0.001"
                className={clases(
                  "h-10 w-full rounded-md border bg-[var(--superficie)] px-3 text-sm tabular-nums outline-none focus:border-[var(--mar)]",
                  invalido && esFuente && "border-[var(--peligro)]",
                )}
                value={campo.valor}
                onChange={(evento) =>
                  alCambiar(cambiarPeso(valor, campo.unidad, evento.target.value))
                }
                onBlur={() => alCambiar(normalizarPeso(valor))}
                aria-invalid={invalido && esFuente}
              />
            </label>
          );
        })}
      </div>

      {invalido ? (
        <p className="text-xs text-[var(--peligro)]" role="alert">
          Indicá un peso mayor que cero en kilos o libras.
        </p>
      ) : null}
    </div>
  );
}
