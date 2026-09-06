"use client";

import { Clock, Download, FileText, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { ExportacionBls } from "@/components/documentos/progreso-exportacion";
import { AvisoError } from "@/components/ui/aviso-error";
import {
  formatearTamano,
  useDescargar,
  useDocumentosDeDespacho,
  useSubirDocumentoDeDespacho,
} from "@/features/documentos/consultas";
import { estadoSubida, sePuedeDescargar } from "@/features/documentos/vocabulario";
import { useTiposDeDocumento } from "@/features/documentos/tipos";
import type { components } from "@/lib/api/generated";
import { clases, formatearFecha } from "@/lib/utilidades";

type IssuedBy = components["schemas"]["IssuedBy"];

const etiquetaEmisor: Record<IssuedBy, string> = {
  PROVIDER: "Proveedor",
  CLIENT: "Cliente",
  AMVARMAR: "AMVARMAR",
  CARRIER: "Transportista",
  AUTHORITY: "Autoridad",
  OTHER: "Otro",
};

/**
 * El BL y las facturas del despacho.
 *
 * En el sistema viejo eran dos pantallas separadas porque eran dos tablas
 * distintas. Para quien las mira son los papeles del mismo despacho, así que
 * van juntas.
 */
export function DocumentosDespacho({
  dispatchId,
}: {
  dispatchId: string;
}) {
  const { data, isPending, error } = useDocumentosDeDespacho(dispatchId);
  const tipos = useTiposDeDocumento("DISPATCH");
  const subir = useSubirDocumentoDeDespacho(dispatchId);
  const descargar = useDescargar();
  const entrada = useRef<HTMLInputElement>(null);
  const [tipoElegido, setTipoElegido] = useState("");
  const [emisor, setEmisor] = useState<IssuedBy | "">("");
  const [progreso, setProgreso] = useState(0);

  if (isPending) return null;
  if (error) return <AvisoError error={error} />;

  const documentos = data ?? [];
  const hayBls = documentos.some((d) => d.document_type_code === "BL");

  // El catálogo ya viene filtrado por rol y contexto desde el backend.
  const tiposDisponibles = tipos.data ?? [];
  const tipoSeleccionado = tiposDisponibles.find((tipo) => tipo.id === tipoElegido);
  const opcionesEmisor = (tipoSeleccionado?.issued_by_options ?? []) as IssuedBy[];
  const emisorEfectivo = emisor || opcionesEmisor[0];

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

        {hayBls ? <ExportacionBls dispatchId={dispatchId} /> : null}
      </div>

      {subir.error ? <AvisoError error={subir.error} /> : null}
      {descargar.error ? <AvisoError error={descargar.error} /> : null}

      {documentos.length > 0 ? (
        <ul className="divide-y overflow-hidden rounded-lg border bg-[var(--superficie)]">
          {documentos.map((documento) => {
            const descargable = sePuedeDescargar(documento.upload_status);
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
                    {estadoSubida[documento.upload_status] || "No disponible todavía"}
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
              onChange={(evento) => {
                setTipoElegido(evento.target.value);
                setEmisor("");
              }}
            >
              <option value="">¿Qué documento es?</option>
              {tiposDisponibles.map((tipo) => (
                <option key={tipo.id} value={tipo.id}>
                  {tipo.label}
                </option>
              ))}
            </select>
          </label>

          {opcionesEmisor.length > 1 ? (
            <label>
              <span className="mb-1 block text-sm font-medium">Emitido por</span>
              <select
                className="h-10 rounded-md border bg-[var(--superficie)] px-3 text-sm"
                value={emisorEfectivo}
                onChange={(evento) => setEmisor(evento.target.value as IssuedBy)}
              >
                {opcionesEmisor.map((opcion) => (
                  <option key={opcion} value={opcion}>
                    {etiquetaEmisor[opcion]}
                  </option>
                ))}
              </select>
            </label>
          ) : null}

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
              if (archivo && tipoElegido && emisorEfectivo) {
                setProgreso(0);
                await subir.mutateAsync({
                  archivo,
                  tipoId: tipoElegido,
                  issuedBy: emisorEfectivo,
                  onProgress: setProgreso,
                });
                setProgreso(0);
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
            disabled={!tipoElegido || !emisorEfectivo || subir.isPending}
          >
            <Upload className="size-4" aria-hidden="true" />
            {subir.isPending && progreso > 0 ? `Subiendo ${progreso}%` : subir.isPending ? "Subiendo…" : "Elegir archivo"}
          </button>
        </div>
      ) : null}
    </section>
  );
}
