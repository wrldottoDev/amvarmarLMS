import type { InputHTMLAttributes, TextareaHTMLAttributes } from "react";
import { clases } from "@/lib/utilidades";

interface PropiedadesCampo extends InputHTMLAttributes<HTMLInputElement> {
  etiqueta: string;
  error?: string;
  /** Explicación bajo el campo. Se lee junto con la etiqueta, no en lugar de ella. */
  ayuda?: string;
}

export function Campo({ etiqueta, error, ayuda, className, id, ...propiedades }: PropiedadesCampo) {
  const campoId = id ?? propiedades.name;
  // Los dos van en `aria-describedby`: si solo fuera el error, quien use lector
  // de pantalla no se enteraría de la regla hasta después de incumplirla.
  const descripcion = [ayuda && `${campoId}-ayuda`, error && `${campoId}-error`]
    .filter(Boolean)
    .join(" ");

  return (
    <label className="grid gap-1.5 text-sm font-medium text-[var(--texto)]" htmlFor={campoId}>
      {etiqueta}
      <input
        id={campoId}
        className={clases(
          "h-11 w-full rounded-md border bg-[var(--superficie)] px-3 text-[var(--texto)] shadow-sm outline-none placeholder:text-[var(--texto-secundario)] focus:border-[var(--mar)]",
          error && "border-[var(--peligro)]",
          className,
        )}
        aria-invalid={Boolean(error)}
        aria-describedby={descripcion || undefined}
        {...propiedades}
      />
      {ayuda ? (
        <span id={`${campoId}-ayuda`} className="text-xs font-normal text-[var(--texto-secundario)]">
          {ayuda}
        </span>
      ) : null}
      {error ? (
        <span id={`${campoId}-error`} className="text-xs font-normal text-[var(--peligro)]">
          {error}
        </span>
      ) : null}
    </label>
  );
}

interface PropiedadesArea extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  etiqueta: string;
  error?: string;
}

export function AreaTexto({ etiqueta, error, className, id, ...propiedades }: PropiedadesArea) {
  const campoId = id ?? propiedades.name;
  return (
    <label className="grid gap-1.5 text-sm font-medium text-[var(--texto)]" htmlFor={campoId}>
      {etiqueta}
      <textarea
        id={campoId}
        className={clases(
          "min-h-24 w-full resize-y rounded-md border bg-[var(--superficie)] px-3 py-2.5 text-[var(--texto)] shadow-sm outline-none placeholder:text-[var(--texto-secundario)] focus:border-[var(--mar)]",
          error && "border-[var(--peligro)]",
          className,
        )}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${campoId}-error` : undefined}
        {...propiedades}
      />
      {error ? (
        <span id={`${campoId}-error`} className="text-xs font-normal text-[var(--peligro)]">
          {error}
        </span>
      ) : null}
    </label>
  );
}
