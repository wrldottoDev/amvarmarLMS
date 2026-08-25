import { Inbox, LoaderCircle } from "lucide-react";

export function CargandoPagina({ texto = "Cargando" }: { texto?: string }) {
  return (
    <div className="flex min-h-56 items-center justify-center gap-3 text-sm text-[var(--texto-secundario)]" role="status">
      <LoaderCircle className="size-5 animate-spin text-[var(--marca)]" aria-hidden="true" />
      {texto}
    </div>
  );
}

export function EstadoVacio({ titulo, descripcion }: { titulo: string; descripcion: string }) {
  return (
    <div className="flex min-h-52 flex-col items-center justify-center px-6 text-center">
      <span className="mb-3 grid size-10 place-items-center rounded-md bg-[#e8f0f2] text-[var(--mar)]">
        <Inbox className="size-5" aria-hidden="true" />
      </span>
      <h2 className="text-sm font-semibold">{titulo}</h2>
      <p className="mt-1 max-w-md text-sm text-[var(--texto-secundario)]">{descripcion}</p>
    </div>
  );
}
