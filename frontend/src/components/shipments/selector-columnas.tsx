"use client";

import { Columns3, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useGuardarColumnas, usePreferenciaColumnas } from "@/features/shipments/columnas";
import { clases } from "@/lib/utilidades";

/**
 * Elegir qué columnas se ven, como en el sistema viejo.
 *
 * Con trece columnas posibles no es un lujo: quien factura mira CFTS y peso,
 * quien rastrea mira tracking y WR. Obligar a los dos a la misma vista hace que
 * ninguno la tenga cómoda.
 */
export function SelectorColumnas() {
  const [abierto, setAbierto] = useState(false);
  const contenedor = useRef<HTMLDivElement>(null);
  const { data } = usePreferenciaColumnas();
  const guardar = useGuardarColumnas();

  useEffect(() => {
    if (!abierto) return;
    function alTocarFuera(evento: MouseEvent) {
      if (!contenedor.current?.contains(evento.target as Node)) setAbierto(false);
    }
    document.addEventListener("mousedown", alTocarFuera);
    return () => document.removeEventListener("mousedown", alTocarFuera);
  }, [abierto]);

  if (!data) return null;

  const visibles = new Set(data.visibles);

  function alternar(columna: string) {
    if (!data) return;
    const siguiente = visibles.has(columna)
      ? data.visibles.filter((c) => c !== columna)
      : // Se agrega en la posición del catálogo, no al final: así el orden de
        // las columnas es siempre el mismo sin importar en qué orden se
        // marcaron.
        data.disponibles.filter((c) => visibles.has(c.clave) || c.clave === columna).map((c) => c.clave);
    guardar.mutate(siguiente);
  }

  return (
    <div className="relative" ref={contenedor}>
      <button
        type="button"
        className="flex h-10 items-center gap-2 rounded-md border px-3 text-sm font-medium hover:bg-[var(--hover)]"
        onClick={() => setAbierto((v) => !v)}
        aria-expanded={abierto}
      >
        <Columns3 className="size-4" aria-hidden="true" />
        Columnas
        <span className="text-xs text-[var(--texto-secundario)]">
          {data.visibles.length}/{data.disponibles.length}
        </span>
      </button>

      {abierto ? (
        <div className="absolute right-0 z-30 mt-2 w-64 rounded-md border bg-[var(--superficie)] p-1.5 shadow-xl">
          <p className="px-2 py-1.5 text-xs text-[var(--texto-secundario)]">
            Se guarda para vos, no cambia lo que ven los demás.
          </p>

          <ul className="max-h-80 overflow-y-auto">
            {data.disponibles.map((columna) => (
              <li key={columna.clave}>
                <label
                  className={clases(
                    "flex items-center gap-2 rounded px-2 py-2 text-sm",
                    columna.fija ? "opacity-50" : "cursor-pointer hover:bg-[var(--hover)]",
                  )}
                  title={columna.fija ? "Sin esta columna el listado no se puede usar" : undefined}
                >
                  <input
                    type="checkbox"
                    className="size-4 accent-[var(--mar)]"
                    checked={visibles.has(columna.clave)}
                    disabled={columna.fija}
                    onChange={() => alternar(columna.clave)}
                  />
                  {columna.etiqueta}
                </label>
              </li>
            ))}
          </ul>

          <button
            type="button"
            className="mt-1 flex w-full items-center gap-2 rounded px-2 py-2 text-sm text-[var(--mar)] hover:bg-[var(--hover)]"
            onClick={() => guardar.mutate(data.disponibles.map((c) => c.clave))}
          >
            <RotateCcw className="size-3.5" aria-hidden="true" />
            Mostrar todas
          </button>
        </div>
      ) : null}
    </div>
  );
}
