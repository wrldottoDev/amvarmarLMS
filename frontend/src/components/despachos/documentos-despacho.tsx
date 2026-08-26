"use client";

import { Clock, Download, FileText, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { AvisoError } from "@/components/ui/aviso-error";
import {
  formatearTamano,
  useDescargar,
  useDescargarBls,
  useDocumentosDeDespacho,
  useSubirDocumentoDeDespacho,
} from "@/features/documentos/consultas";
import { estadoEscaneo, sePuedeDescargar } from "@/features/documentos/vocabulario";
import { useTiposDeDocumento } from "@/features/documentos/tipos";
import { clases, formatearFecha } from "@/lib/utilidades";

/**
 * El BL y las facturas del despacho.
 *
 * En el sistema viejo eran dos pantallas separadas porque eran dos tablas
 * distintas. Para quien las mira son los papeles del mismo despacho, así que
 * van juntas.
 */
export function DocumentosDespacho({
  dispatchId,
  esCliente,
}: {
  dispatchId: string;
  esCliente: boolean;
}) {
  const { data, isPending, error } = useDocumentosDeDespacho(dispatchId);
  const tipos = useTiposDeDocumento();
  const subir = useSubirDocumentoDeDespacho(dispatchId);
  const descargar = useDescargar();
  const descargarBls = useDescargarBls(dispatchId);
  const entrada = useRef<HTMLInputElement>(null);
  const [tipoElegido, setTipoElegido] = useState("");

  if (isPending) return null;
  if (error) return <AvisoError error={error} />;

  const documentos = data ?? [];
  const hayBls = documentos.some((d) => d.document_type_code === "BL");

  // Operaciones sube el BL; el cliente sus facturas. Se filtra el selector en
  // vez de dejar elegir algo que el servidor va a rechazar.
  const tiposDisponibles = (tipos.data ?? []).filter((t) =>
    esCliente ? t.provided_by === "CLIENT" : true,
  );

  async function abrir(documentoId: string) {
    const enlace = await descargar.mutateAsync(documentoId);
    window.open(enlace.url, "_blank", "noopener,noreferrer");
  }

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <FileText className="size-5 text-[var(--marca)]" aria-hidden="true" />
          <h2 className="text-base font-bold">Documentos del despacho</h2>
        </div>

        {hayBls ? (
          <button
            type="button"
            className="flex h-9 items-center gap-1.5 rounded-md border px-3 text-sm font-medium hover:bg-[var(--hover)]"
            onClick={() => descargarBls.mutate()}
            disabled={descargarBls.isPending}
            title="Todos los Bills of Lading juntos, para mandárselos al agente"
          >
            <Download className="size-4" aria-hidden="true" />
            {descargarBls.isPending ? "Preparando…" : "Descargar BLs"}
          </button>
        ) : null}
      </div>

      {subir.error ? <AvisoError error={subir.error} /> : null}
      {descargarBls.error ? <AvisoError error={descargarBls.error} /> : null}
      {descargar.error ? <AvisoError error={descargar.error} /> : null}

      {documentos.length > 0 ? (
        <ul className="divide-y overflow-hidden rounded-lg border bg-[var(--superficie)]">
          {documentos.map((documento) => {
            const descargable = sePuedeDescargar(documento.scan_status, documento.upload_status);
            return (
              <li key={documento.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <span className="min-w-0 flex-1">
                  <strong className="block truncate text-sm">{documento.original_name}</strong>
                  <span className="block text-xs text-[var(--texto-secundario)]">
                    {documento.document_type_label} · {formatearTamano(documento.size_bytes)} ·{" "}
                    {formatearFecha(documento.created_at)}
                  </span>
                </span>

                {descargable ? (
                  <button
                    type="button"
                    className="flex h-9 shrink-0 items-center gap-1.5 rounded-md border px-3 text-sm font-medium hover:bg-[var(--hover)]"
                    onClick={() => void abrir(documento.id)}
                    disabled={descargar.isPending}
                  >
                    <Download className="size-4" aria-hidden="true" />
                    Ver
                  </button>
                ) : (
                  <span className="flex shrink-0 items-center gap-1.5 text-xs text-[var(--texto-secundario)]">
                    <Clock className="size-3.5" aria-hidden="true" />
                    {estadoEscaneo[documento.scan_status] || "No disponible todavía"}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="rounded-lg border bg-[var(--superficie)] px-4 py-6 text-center text-sm text-[var(--texto-secundario)]">
          Todavía no hay documentos en este despacho.
        </p>
      )}

      {tiposDisponibles.length > 0 ? (
        <div className="flex flex-wrap items-end gap-2 rounded-lg border bg-[var(--superficie)] px-4 py-3">
          <label className="min-w-48 flex-1">
            <span className="mb-1 block text-sm font-medium">Adjuntar documento</span>
            <select
              className="w-full rounded-md border px-3 py-2 text-sm"
              value={tipoElegido}
              onChange={(evento) => setTipoElegido(evento.target.value)}
            >
              <option value="">¿Qué documento es?</option>
              {tiposDisponibles.map((tipo) => (
                <option key={tipo.id} value={tipo.id}>
                  {tipo.label}
                </option>
              ))}
            </select>
          </label>

          <input
            ref={entrada}
            type="file"
            className="hidden"
            accept={(tiposDisponibles
              .find((t) => t.id === tipoElegido)
              ?.allowed_formats ?? [])
              .map((f) => `.${f.toLowerCase()}`)
              .join(",")}
            onChange={async (evento) => {
              const archivo = evento.target.files?.[0];
              evento.target.value = "";
              if (archivo && tipoElegido) {
                await subir.mutateAsync({ archivo, tipoId: tipoElegido });
              }
            }}
          />
          <button
            type="button"
            className={clases(
              "flex h-10 items-center gap-1.5 rounded-md bg-[var(--mar)] px-4 text-sm font-semibold text-white hover:opacity-90",
              (!tipoElegido || subir.isPending) && "opacity-60",
            )}
            onClick={() => entrada.current?.click()}
            disabled={!tipoElegido || subir.isPending}
          >
            <Upload className="size-4" aria-hidden="true" />
            {subir.isPending ? "Subiendo…" : "Elegir archivo"}
          </button>
        </div>
      ) : null}
    </section>
  );
}
