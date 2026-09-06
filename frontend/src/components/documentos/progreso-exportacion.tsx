"use client";

import { Archive, Download, LoaderCircle, RotateCcw } from "lucide-react";
import { AvisoError } from "@/components/ui/aviso-error";
import {
  formatearTamano,
  useExportacionBls,
  useExportacionCarga,
} from "@/features/documentos/consultas";

type Exportacion = ReturnType<typeof useExportacionCarga>;

function ControlExportacion({
  exportacion,
  etiqueta,
}: {
  exportacion: Exportacion;
  etiqueta: string;
}) {
  const lista = exportacion.job?.status === "READY";
  const reintentar = ["FAILED", "EXPIRED"].includes(exportacion.job?.status ?? "");

  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      {exportacion.error ? <AvisoError error={exportacion.error} /> : null}
      {lista && exportacion.job?.size_bytes ? (
        <span className="text-xs tabular-nums text-[var(--texto-secundario)]">
          {formatearTamano(exportacion.job.size_bytes)}
        </span>
      ) : null}
      <button
        type="button"
        className="flex h-9 items-center gap-1.5 rounded-md border px-3 text-sm font-medium hover:bg-[var(--hover)] disabled:cursor-wait disabled:opacity-60"
        onClick={() => {
          if (lista) exportacion.descargar();
          else exportacion.solicitar();
        }}
        disabled={exportacion.preparando || exportacion.descargando}
      >
        {exportacion.preparando ? (
          <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
        ) : lista ? (
          <Download className="size-4" aria-hidden="true" />
        ) : reintentar ? (
          <RotateCcw className="size-4" aria-hidden="true" />
        ) : (
          <Archive className="size-4" aria-hidden="true" />
        )}
        {exportacion.preparando
          ? "Preparando ZIP"
          : lista
            ? `Descargar ${etiqueta}`
            : reintentar
              ? "Preparar de nuevo"
              : `Preparar ${etiqueta}`}
      </button>
    </div>
  );
}

export function ExportacionCarga({ cargaId }: { cargaId: string }) {
  return <ControlExportacion exportacion={useExportacionCarga(cargaId)} etiqueta="expediente" />;
}

export function ExportacionBls({ dispatchId }: { dispatchId: string }) {
  return <ControlExportacion exportacion={useExportacionBls(dispatchId)} etiqueta="BLs" />;
}
