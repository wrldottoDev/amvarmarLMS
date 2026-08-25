import { clases } from "@/lib/utilidades";
import { etiquetaDespacho, tonoDespacho } from "@/features/despachos/catalogo";

const estilos = {
  espera: "bg-[#fff6e5] text-[#8a5b00] border-[#f2d9a0]",
  avance: "bg-[#e8f0f2] text-[var(--mar)] border-[#bcd6dd]",
  listo: "bg-[#e9f6ec] text-[#1c6b33] border-[#b7e0c2]",
  alto: "bg-[#fff2f0] text-[#82231b] border-[#f0b8b3]",
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
