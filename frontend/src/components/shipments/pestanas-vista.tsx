import Link from "next/link";
import { clases } from "@/lib/utilidades";

/** Pestañas de una sección (Miami, Reportes de tránsito), en `?vista=`. */
export function PestanasVista({
  ruta,
  etiqueta,
  pestanas,
  actual,
  ayuda,
}: {
  ruta: string;
  etiqueta: string;
  pestanas: readonly { clave: string; titulo: string }[];
  actual: string;
  ayuda: string;
}) {
  return (
    <div className="space-y-2">
      {/* Línea base como sombra interna: con overflow-x (celular), un borde con
          `-mb-px` en las pestañas queda recortado. */}
      <nav
        className="flex gap-1 overflow-x-auto shadow-[inset_0_-1px_0_var(--borde)]"
        aria-label={etiqueta}
      >
        {pestanas.map((pestana) => (
          <Link
            key={pestana.clave}
            href={`${ruta}?vista=${pestana.clave}`}
            aria-current={pestana.clave === actual ? "page" : undefined}
            // `!`: `* { border-color }` de globals.css va fuera de capa y pisa los colores de borde.
            className={clases(
              "shrink-0 whitespace-nowrap border-b-2 px-4 py-2 text-sm font-semibold",
              pestana.clave === actual
                ? "border-[var(--mar)]! text-[var(--mar)]"
                : "border-transparent! text-[var(--texto-secundario)] hover:text-[var(--texto)]",
            )}
          >
            {pestana.titulo}
          </Link>
        ))}
      </nav>
      <p className="text-sm text-[var(--texto-secundario)]">{ayuda}</p>
    </div>
  );
}
