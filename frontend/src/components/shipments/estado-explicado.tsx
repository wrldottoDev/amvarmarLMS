import { ArrowRight, Info } from "lucide-react";
import Link from "next/link";
import { esEstadoCarga } from "@/features/shipments/catalogo-estados";
import { queHacerAhora, queSignifica } from "@/features/shipments/vocabulario";

/**
 * Traduce el estado a una frase y, si corresponde, ofrece la acción.
 *
 * Un cliente que ve "Almacenada" tiene que saber ya qué significa eso y qué
 * puede hacer. Poner la explicación al lado del estado evita que tenga que
 * preguntar o aprenderse el flujo.
 *
 * Solo se muestra al cliente: Operaciones conoce el catálogo de memoria y para
 * ellos sería ruido en cada pantalla.
 */
export function EstadoExplicado({
  estado,
  esCliente,
  puedeDespachar,
}: {
  // El backend tipa `status` como texto libre; acá se acota al catálogo
  // conocido y se sale sin romper si algún día llega uno nuevo.
  estado: string;
  esCliente: boolean;
  puedeDespachar: boolean;
}) {
  if (!esCliente) return null;
  if (!esEstadoCarga(estado)) return null;

  const significado = queSignifica[estado];
  const siguiente = queHacerAhora[estado];

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-[#f4f9fa] px-4 py-3.5">
      <Info className="size-5 shrink-0 text-[var(--mar)]" aria-hidden="true" />
      <p className="min-w-0 flex-1 text-sm">
        {significado}
        {siguiente ? <span className="block text-[var(--texto-secundario)]">{siguiente}</span> : null}
      </p>

      {estado === "STORED" && puedeDespachar ? (
        <Link
          href="/despachos/nuevo"
          className="flex h-9 shrink-0 items-center gap-1.5 rounded-md bg-[var(--mar)] px-3.5 text-sm font-semibold text-white hover:opacity-90"
        >
          Solicitar despacho
          <ArrowRight className="size-4" aria-hidden="true" />
        </Link>
      ) : null}
    </div>
  );
}
