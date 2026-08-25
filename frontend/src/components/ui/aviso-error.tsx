import { AlertCircle } from "lucide-react";
import { ErrorApi } from "@/lib/api/client";

export function AvisoError({ error }: { error: unknown }) {
  const mensaje = error instanceof Error ? error.message : "Ocurrió un error inesperado.";
  const requestId = error instanceof ErrorApi && error.status >= 500 ? error.requestId : undefined;

  return (
    <div className="flex gap-3 rounded-md border border-[#f0b8b3] bg-[#fff2f0] px-4 py-3 text-sm text-[#82231b]" role="alert">
      <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div>
        <p>{mensaje}</p>
        {requestId ? <p className="mt-1 font-mono text-xs">Referencia: {requestId}</p> : null}
      </div>
    </div>
  );
}
