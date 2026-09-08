"use client";

import { X } from "lucide-react";
import { useEffect } from "react";

export function Modal({
  abierto,
  cerrar,
  titulo,
  children,
}: Readonly<{
  abierto: boolean;
  cerrar: () => void;
  titulo: string;
  children: React.ReactNode;
}>) {
  useEffect(() => {
    if (!abierto) return;
    const alPresionar = (evento: KeyboardEvent) => {
      if (evento.key === "Escape") cerrar();
    };
    document.addEventListener("keydown", alPresionar);
    return () => document.removeEventListener("keydown", alPresionar);
  }, [abierto, cerrar]);

  if (!abierto) return null;

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-[var(--texto)]/50 p-4" onMouseDown={cerrar}>
      <section
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg border bg-[var(--superficie)] shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="titulo-modal"
        onMouseDown={(evento) => evento.stopPropagation()}
      >
        <header className="flex items-center justify-between border-b px-5 py-4">
          <h2 id="titulo-modal" className="text-base font-semibold">
            {titulo}
          </h2>
          <button
            type="button"
            className="grid size-9 place-items-center rounded-md text-[var(--texto-secundario)] hover:bg-[var(--hover)]"
            onClick={cerrar}
            title="Cerrar"
            aria-label="Cerrar"
          >
            <X className="size-5" aria-hidden="true" />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
