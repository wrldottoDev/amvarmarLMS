"use client";

import { AlertTriangle, Clock, Download, FileText, Upload } from "lucide-react";
import { useRef, useState } from "react";
import {
  formatearTamano,
  useDescargar,
  useExpediente,
  useSubirDocumento,
} from "@/features/documentos/consultas";
import {
  estadoEscaneo,
  estadoRequisito,
  sePuedeDescargar,
} from "@/features/documentos/vocabulario";
import { AvisoError } from "@/components/ui/aviso-error";
import { clases, formatearFecha } from "@/lib/utilidades";
import type { RequisitoDocumental } from "@/lib/api/tipos";

const tonos = {
  falta: "border-[#f2d9a0] bg-[#fff6e5] text-[#8a5b00]",
  espera: "border-[#bcd6dd] bg-[#e8f0f2] text-[var(--mar)]",
  listo: "border-[#b7e0c2] bg-[#e9f6ec] text-[#1c6b33]",
  alto: "border-[#f0b8b3] bg-[#fff2f0] text-[#82231b]",
} as const;

export function Expediente({ cargaId, esCliente }: { cargaId: string; esCliente: boolean }) {
  const { data, isPending, error } = useExpediente(cargaId);
  const subir = useSubirDocumento(cargaId);
  const descargar = useDescargar();
  const [subiendo, setSubiendo] = useState<string | null>(null);

  if (isPending) return null;
  if (error) return <AvisoError error={error} />;
  if (!data) return null;

  // Al cliente solo se le piden los documentos que le tocan a él. Un packing
  // list lo carga Operaciones, y mostrárselo como pendiente suyo lo haría
  // buscar un archivo que nunca tuvo (ADR-0003).
  const requisitos = esCliente
    ? data.requisitos.filter((r) => r.required_from === "CLIENT")
    : data.requisitos;

  const faltantes = requisitos.filter((r) => ["PENDING", "REJECTED", "OPEN"].includes(r.status));

  async function alElegirArchivo(requisito: RequisitoDocumental, archivo: File | undefined) {
    if (!archivo || !requisito.document_type_id) return;
    setSubiendo(requisito.id);
    try {
      await subir.mutateAsync({ archivo, tipoId: requisito.document_type_id });
    } finally {
      setSubiendo(null);
    }
  }

  async function abrir(documentoId: string) {
    const enlace = await descargar.mutateAsync(documentoId);
    // Pestaña nueva: si el usuario vuelve atrás no pierde la página de la carga.
    window.open(enlace.url, "_blank", "noopener,noreferrer");
  }

  return (
    <section aria-labelledby="documentos-carga" className="space-y-4">
      <div className="flex items-center gap-2">
        <FileText className="size-5 text-[var(--marca)]" aria-hidden="true" />
        <h2 id="documentos-carga" className="text-base font-bold">
          Documentos
        </h2>
      </div>

      {esCliente ? (
        <p
          className={clases(
            "rounded-md border px-4 py-3 text-sm",
            faltantes.length > 0 ? tonos.falta : tonos.listo,
          )}
        >
          {faltantes.length === 0
            ? "No falta ningún documento de tu parte."
            : faltantes.length === 1
              ? "Falta 1 documento. Sin él no podemos despachar esta carga."
              : `Faltan ${faltantes.length} documentos. Sin ellos no podemos despachar esta carga.`}
        </p>
      ) : null}

      {subir.error ? <AvisoError error={subir.error} /> : null}
      {descargar.error ? <AvisoError error={descargar.error} /> : null}

      {requisitos.length > 0 ? (
        <ul className="divide-y overflow-hidden rounded-lg border bg-white">
          {requisitos.map((requisito) => {
            const estado = estadoRequisito[requisito.status] ?? {
              etiqueta: requisito.status,
              explicacion: "",
              tono: "espera" as const,
            };
            const puedeSubir =
              esCliente && ["PENDING", "REJECTED", "OPEN"].includes(requisito.status);
            const enProgreso = subiendo === requisito.id;

            return (
              <li key={requisito.id} className="flex flex-wrap items-center gap-3 px-4 py-4">
                <span className="min-w-0 flex-1">
                  <strong className="block text-sm">{requisito.label}</strong>
                  <span className="block text-xs text-[var(--texto-secundario)]">
                    {estado.explicacion}
                    {requisito.allowed_formats.length > 0 && puedeSubir
                      ? ` Aceptamos ${requisito.allowed_formats.join(", ")}.`
                      : ""}
                  </span>
                </span>

                <span
                  className={clases(
                    "shrink-0 rounded-full border px-2.5 py-0.5 text-xs font-semibold",
                    tonos[estado.tono],
                  )}
                >
                  {estado.etiqueta}
                </span>

                {requisito.document_id ? (
                  <button
                    type="button"
                    className="flex h-9 shrink-0 items-center gap-1.5 rounded-md border px-3 text-sm font-medium hover:bg-[#edf1f2]"
                    onClick={() => void abrir(requisito.document_id as string)}
                    disabled={descargar.isPending}
                  >
                    <Download className="size-4" aria-hidden="true" />
                    Ver
                  </button>
                ) : null}

                {puedeSubir ? (
                  <BotonSubir
                    enProgreso={enProgreso}
                    formatos={requisito.allowed_formats}
                    onElegir={(archivo) => void alElegirArchivo(requisito, archivo)}
                  />
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="rounded-lg border bg-white px-4 py-8 text-center text-sm text-[var(--texto-secundario)]">
          Esta carga no tiene documentos pendientes.
        </p>
      )}

      {data.documentos.length > 0 ? (
        <details className="rounded-lg border bg-white">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
            Todos los archivos ({data.documentos.length})
          </summary>
          <ul className="divide-y border-t">
            {data.documentos.map((documento) => {
              const aviso = estadoEscaneo[documento.scan_status] ?? "";
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
                      className="flex h-9 shrink-0 items-center gap-1.5 rounded-md border px-3 text-sm font-medium hover:bg-[#edf1f2]"
                      onClick={() => void abrir(documento.id)}
                      disabled={descargar.isPending}
                    >
                      <Download className="size-4" aria-hidden="true" />
                      Descargar
                    </button>
                  ) : (
                    <span
                      className={clases(
                        "flex shrink-0 items-center gap-1.5 text-xs",
                        documento.scan_status === "INFECTED"
                          ? "text-[#82231b]"
                          : "text-[var(--texto-secundario)]",
                      )}
                    >
                      {documento.scan_status === "INFECTED" ? (
                        <AlertTriangle className="size-3.5" aria-hidden="true" />
                      ) : (
                        <Clock className="size-3.5" aria-hidden="true" />
                      )}
                      {aviso || "No disponible todavía"}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function BotonSubir({
  enProgreso,
  formatos,
  onElegir,
}: {
  enProgreso: boolean;
  formatos: string[];
  onElegir: (archivo: File | undefined) => void;
}) {
  const entrada = useRef<HTMLInputElement>(null);

  return (
    <>
      <input
        ref={entrada}
        type="file"
        className="hidden"
        // Filtra el selector del sistema para que ni siquiera se pueda elegir
        // algo que el servidor va a rechazar.
        accept={formatos.map((f) => `.${f.toLowerCase()}`).join(",")}
        onChange={(evento) => {
          onElegir(evento.target.files?.[0]);
          // Permite volver a elegir el mismo archivo si el primer intento falló.
          evento.target.value = "";
        }}
      />
      <button
        type="button"
        className="flex h-9 shrink-0 items-center gap-1.5 rounded-md bg-[var(--mar)] px-3.5 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-60"
        onClick={() => entrada.current?.click()}
        disabled={enProgreso}
      >
        {enProgreso ? (
          <>
            <Clock className="size-4 animate-pulse" aria-hidden="true" />
            Subiendo…
          </>
        ) : (
          <>
            <Upload className="size-4" aria-hidden="true" />
            Subir
          </>
        )}
      </button>
    </>
  );
}
