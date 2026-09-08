import type { ButtonHTMLAttributes } from "react";
import { LoaderCircle } from "lucide-react";
import { clases } from "@/lib/utilidades";

type Variante = "primario" | "secundario" | "peligro" | "texto";

interface PropiedadesBoton extends ButtonHTMLAttributes<HTMLButtonElement> {
  variante?: Variante;
  cargando?: boolean;
}

const variantes: Record<Variante, string> = {
  primario: "border-transparent bg-[var(--mar)] text-white hover:bg-[var(--mar-oscuro)]",
  secundario: "border-[var(--borde)] bg-[var(--superficie)] text-[var(--texto)] hover:bg-[var(--hover)]",
  peligro: "border-transparent bg-[var(--peligro)] text-white hover:bg-[var(--peligro)]",
  texto: "border-transparent bg-transparent text-[var(--mar)] hover:bg-[var(--marca-tenue)]",
};

export function Boton({
  className,
  variante = "primario",
  cargando = false,
  disabled,
  children,
  ...propiedades
}: PropiedadesBoton) {
  return (
    <button
      className={clases(
        "inline-flex min-h-10 items-center justify-center gap-2 rounded-md border px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-55",
        variantes[variante],
        className,
      )}
      disabled={disabled || cargando}
      {...propiedades}
    >
      {cargando ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : null}
      {children}
    </button>
  );
}
