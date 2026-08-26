import { clases } from "@/lib/utilidades";
import { etiquetaDespacho, tonoDespacho } from "@/features/despachos/catalogo";

const estilos = {
  espera: "bg-[var(--advertencia-tenue)] text-[var(--advertencia)] border-[var(--advertencia-borde)]",
  avance: "bg-[var(--marca-tenue)] text-[var(--mar)] border-[var(--marca)]",
  listo: "bg-[var(--exito-tenue)] text-[var(--exito)] border-[var(--exito-borde)]",
  alto: "bg-[var(--peligro-tenue)] text-[var(--peligro)] border-[var(--peligro-borde)]",
} as const;

export function InsigniaDespacho({ estado }: { estado: string }) {
  const tono = tonoDespacho[estado] ?? "espera";
  return (
    <span
      className={clases(
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold",
        estilos[tono],
      )}
    >
      {etiquetaDespacho[estado] ?? estado}
    </span>
  );
}
