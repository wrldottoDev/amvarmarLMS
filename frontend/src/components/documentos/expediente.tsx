"use client";

import { AlertTriangle, Clock, Download, FileText, Pencil, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { ExportacionCarga } from "@/components/documentos/progreso-exportacion";
import {
  formatearTamano,
  useDescargar,
  useExpediente,
  useQuitarDocumento,
  useRenombrarDocumento,
  useSubirDocumento,
} from "@/features/documentos/consultas";
import {
  estadoSubida,
  estadoRequisito,
  sePuedeDescargar,
} from "@/features/documentos/vocabulario";
import { AvisoError } from "@/components/ui/aviso-error";
import { Modal } from "@/components/ui/modal";
import { clases, formatearFecha } from "@/lib/utilidades";
import type { components } from "@/lib/api/generated";
import type { RequisitoDocumental } from "@/lib/api/tipos";

type IssuedBy = components["schemas"]["IssuedBy"];

const etiquetaEmisor: Record<IssuedBy, string> = {
  PROVIDER: "Proveedor",
  CLIENT: "Cliente",
  AMVARMAR: "AMVARMAR",
  CARRIER: "Transportista",
  AUTHORITY: "Autoridad",
  OTHER: "Otro",
};

const tonos = {
  falta: "border-[var(--advertencia-borde)] bg-[var(--advertencia-tenue)] text-[var(--advertencia)]",
  espera: "border-[var(--marca)] bg-[var(--marca-tenue)] text-[var(--mar)]",
  listo: "border-[var(--exito-borde)] bg-[var(--exito-tenue)] text-[var(--exito)]",
  alto: "border-[var(--peligro-borde)] bg-[var(--peligro-tenue)] text-[var(--peligro)]",
} as const;

export function Expediente({
  cargaId,
  esCliente,
  soloLectura = false,
}: {
  cargaId: string;
  esCliente: boolean;
  /** Historial de despachos (ADR-0007): una carga archivada no admite
   * documentos nuevos, renombres ni bajas — el backend lo rechaza igual. */
  soloLectura?: boolean;
}) {
  const { data, isPending, error } = useExpediente(cargaId);
  const subir = useSubirDocumento(cargaId);
  const descargar = useDescargar();
  const renombrar = useRenombrarDocumento(cargaId);
  const quitar = useQuitarDocumento(cargaId);

  const [subiendo, setSubiendo] = useState<string | null>(null);
  const [progreso, setProgreso] = useState(0);
  const [emisores, setEmisores] = useState<Record<string, IssuedBy>>({});
  const [tipoLibre, setTipoLibre] = useState("");
  const [emisorLibre, setEmisorLibre] = useState<IssuedBy | "">("");
  const [renombrando, setRenombrando] = useState<string | null>(null);
  const [nombreNuevo, setNombreNuevo] = useState("");
  const [aQuitar, setAQuitar] = useState<{ id: string; original_name: string } | null>(null);
  const [motivoQuitar, setMotivoQuitar] = useState("");

  // Renombrar y quitar son de personal interno, igual que `edit_files` del
  // sistema viejo, que estaba bajo `@staff_member_required`.
  const esInterno = !esCliente;

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

  async function guardarNombre(documentoId: string) {
    await renombrar.mutateAsync({ id: documentoId, nombre: nombreNuevo.trim() });
    setRenombrando(null);
  }

  async function alElegirArchivo(
    requisito: RequisitoDocumental,
    archivo: File | undefined,
    issuedBy: IssuedBy,
  ) {
    if (!archivo || !requisito.document_type_id) return;
    setSubiendo(requisito.id);
    setProgreso(0);
    try {
      await subir.mutateAsync({
        archivo,
        tipoId: requisito.document_type_id,
        issuedBy,
        onProgress: setProgreso,
      });
    } finally {
      setSubiendo(null);
      setProgreso(0);
    }
  }

  async function abrir(documentoId: string) {
    const enlace = await descargar.mutateAsync(documentoId);
    // Pestaña nueva: si el usuario vuelve atrás no pierde la página de la carga.
    window.open(enlace.url, "_blank", "noopener,noreferrer");
  }

  const tipoLibreSeleccionado = data.tipos.find((tipo) => tipo.id === tipoLibre);
  const opcionesEmisorLibre = (tipoLibreSeleccionado?.issued_by_options ?? []) as IssuedBy[];
  const emisorLibreEfectivo = emisorLibre || opcionesEmisorLibre[0];

  async function alElegirArchivoLibre(archivo: File | undefined) {
    if (!archivo || !tipoLibreSeleccionado || !emisorLibreEfectivo) return;
    setSubiendo("libre");
    setProgreso(0);
    try {
      await subir.mutateAsync({
        archivo,
        tipoId: tipoLibreSeleccionado.id,
        issuedBy: emisorLibreEfectivo,
        onProgress: setProgreso,
      });
    } finally {
      setSubiendo(null);
      setProgreso(0);
    }
  }

  return (
    <section aria-labelledby="documentos-carga" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <FileText className="size-5 text-[var(--marca)]" aria-hidden="true" />
          <h2 id="documentos-carga" className="text-base font-bold">
            Documentos
          </h2>
        </div>

        {data.documentos.length > 1 ? <ExportacionCarga cargaId={cargaId} /> : null}
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
        <ul className="divide-y overflow-hidden rounded-lg border bg-[var(--superficie)]">
          {requisitos.map((requisito) => {
            const estado = estadoRequisito[requisito.status] ?? {
              etiqueta: requisito.status,
              explicacion: "",
              tono: "espera" as const,
            };
            const tipo = data.tipos.find((item) => item.id === requisito.document_type_id);
            const opcionesEmisor = (tipo?.issued_by_options ?? []) as IssuedBy[];
            const emisor = emisores[requisito.id] ?? opcionesEmisor[0];
            const puedeSubir =
              !soloLectura &&
              Boolean(tipo) &&
              ["PENDING", "REJECTED", "OPEN"].includes(requisito.status);
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
                    className="flex h-9 shrink-0 items-center gap-1.5 rounded-md border px-3 text-sm font-medium hover:bg-[var(--hover)]"
                    onClick={() => void abrir(requisito.document_id as string)}
                    disabled={descargar.isPending}
                  >
                    <Download className="size-4" aria-hidden="true" />
                    Ver
                  </button>
                ) : null}

                {puedeSubir ? (
                  <span className="flex flex-wrap items-center justify-end gap-2">
                    {opcionesEmisor.length > 1 ? (
                      <label className="text-xs text-[var(--texto-secundario)]">
                        Emitido por
                        <select
                          className="ml-1 h-9 rounded-md border bg-[var(--superficie)] px-2 text-sm text-[var(--texto)]"
                          value={emisor}
                          onChange={(evento) =>
                            setEmisores((actuales) => ({
                              ...actuales,
                              [requisito.id]: evento.target.value as IssuedBy,
                            }))
                          }
                        >
                          {opcionesEmisor.map((opcion) => (
                            <option key={opcion} value={opcion}>
                              {etiquetaEmisor[opcion]}
                            </option>
                          ))}
                        </select>
                      </label>
                    ) : null}
                    <BotonSubir
                      enProgreso={enProgreso}
                      progreso={enProgreso ? progreso : 0}
                      formatos={requisito.allowed_formats}
                      onElegir={(archivo) =>
                        emisor && void alElegirArchivo(requisito, archivo, emisor)
                      }
                    />
                  </span>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="rounded-lg border bg-[var(--superficie)] px-4 py-8 text-center text-sm text-[var(--texto-secundario)]">
          Esta carga no tiene documentos pendientes.
        </p>
      )}

      {data.tipos.length > 0 && !soloLectura ? (
        <div className="flex flex-wrap items-end gap-2 border-y bg-[var(--superficie)] px-4 py-3">
          <label className="min-w-56 flex-1">
            <span className="mb-1 block text-sm font-medium">Adjuntar otro documento</span>
            <select
              className="h-10 w-full rounded-md border bg-[var(--superficie)] px-3 text-sm"
              value={tipoLibre}
              onChange={(evento) => {
                setTipoLibre(evento.target.value);
                setEmisorLibre("");
              }}
            >
              <option value="">Seleccioná el tipo</option>
              {data.tipos.map((tipo) => (
                <option key={tipo.id} value={tipo.id}>
                  {tipo.label}
                </option>
              ))}
            </select>
          </label>

          {opcionesEmisorLibre.length > 1 ? (
            <label>
              <span className="mb-1 block text-sm font-medium">Emitido por</span>
              <select
                className="h-10 rounded-md border bg-[var(--superficie)] px-3 text-sm"
                value={emisorLibreEfectivo}
                onChange={(evento) => setEmisorLibre(evento.target.value as IssuedBy)}
              >
                {opcionesEmisorLibre.map((opcion) => (
                  <option key={opcion} value={opcion}>
                    {etiquetaEmisor[opcion]}
                  </option>
                ))}
              </select>
            </label>
          ) : null}

          <BotonSubir
            enProgreso={subiendo === "libre"}
            progreso={subiendo === "libre" ? progreso : 0}
            formatos={tipoLibreSeleccionado?.allowed_formats ?? []}
            onElegir={(archivo) => void alElegirArchivoLibre(archivo)}
            deshabilitado={!tipoLibreSeleccionado || !emisorLibreEfectivo}
          />
        </div>
      ) : null}

      {data.documentos.length > 0 ? (
        <details className="rounded-lg border bg-[var(--superficie)]">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
            Todos los archivos ({data.documentos.length})
          </summary>
          <ul className="divide-y border-t">
            {data.documentos.map((documento) => {
              const aviso = estadoSubida[documento.upload_status] ?? "";
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

                  {/* Renombrar y quitar son de personal interno, igual que en
                      `edit_files` del sistema viejo. El cliente ve sus archivos
                      y los descarga; no reorganiza el expediente. */}
                  {esInterno && !soloLectura ? (
                    <span className="flex shrink-0 gap-1">
                      <button
                        type="button"
                        className="grid size-9 place-items-center rounded-md border hover:bg-[var(--hover)]"
                        onClick={() => {
                          setRenombrando(documento.id);
                          setNombreNuevo(documento.original_name);
                        }}
                        aria-label={`Renombrar ${documento.original_name}`}
                        title="Renombrar"
                      >
                        <Pencil className="size-4" aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="grid size-9 place-items-center rounded-md border text-[var(--peligro)] hover:bg-[var(--peligro-tenue)]"
                        onClick={() => {
                          setAQuitar(documento);
                          setMotivoQuitar("");
                        }}
                        aria-label={`Quitar ${documento.original_name}`}
                        title="Quitar del expediente"
                      >
                        <Trash2 className="size-4" aria-hidden="true" />
                      </button>
                    </span>
                  ) : null}

                  {descargable ? (
                    <button
                      type="button"
                      className="flex h-9 shrink-0 items-center gap-1.5 rounded-md border px-3 text-sm font-medium hover:bg-[var(--hover)]"
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
                        documento.upload_status === "FAILED"
                          ? "text-[var(--peligro)]"
                          : "text-[var(--texto-secundario)]",
                      )}
                    >
                      {documento.upload_status === "FAILED" ? (
                        <AlertTriangle className="size-3.5" aria-hidden="true" />
                      ) : (
                        <Clock className="size-3.5" aria-hidden="true" />
                      )}
                      {aviso || "No disponible todavía"}
                    </span>
                  )}

                  {renombrando === documento.id ? (
                    <form
                      className="flex w-full gap-2"
                      onSubmit={(evento) => {
                        evento.preventDefault();
                        void guardarNombre(documento.id);
                      }}
                    >
                      <input
                        className="flex-1 rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
                        value={nombreNuevo}
                        autoFocus
                        onChange={(evento) => setNombreNuevo(evento.target.value)}
                      />
                      <button
                        type="submit"
                        className="h-10 rounded-md bg-[var(--marca)] px-3 text-sm font-medium text-white disabled:opacity-50"
                        disabled={!nombreNuevo.trim() || renombrar.isPending}
                      >
                        Guardar
                      </button>
                      <button
                        type="button"
                        className="h-10 rounded-md border px-3 text-sm font-medium hover:bg-[var(--hover)]"
                        onClick={() => setRenombrando(null)}
                      >
                        Cancelar
                      </button>
                    </form>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </details>
      ) : null}

      {/* Confirmación explícita: quitar un documento puede reabrir el requisito
          que bloquea el despacho, y eso no debería pasar por un clic al pasar. */}
      <Modal
        abierto={aQuitar !== null}
        titulo="¿Quitar este documento del expediente?"
        cerrar={() => setAQuitar(null)}
      >
        <p className="text-sm">
          <strong>{aQuitar?.original_name}</strong> deja de aparecer en el expediente. Si era el
          único de su tipo, el requisito que satisfacía vuelve a quedar pendiente y la carga no
          podrá despacharse hasta que se reponga.
        </p>
        <p className="mt-2 text-sm text-[var(--texto-secundario)]">
          El archivo no se borra del almacenamiento: queda como constancia de que estuvo.
        </p>
        <label className="mt-3 block text-sm font-medium">
          Motivo de la invalidación
          <textarea
            className="mt-1 w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
            rows={2}
            maxLength={1000}
            value={motivoQuitar}
            onChange={(evento) => setMotivoQuitar(evento.target.value)}
          />
        </label>

        {quitar.error ? (
          <div className="mt-3">
            <AvisoError error={quitar.error} />
          </div>
        ) : null}

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            className="h-10 rounded-md border px-4 text-sm font-medium hover:bg-[var(--hover)]"
            onClick={() => setAQuitar(null)}
          >
            Cancelar
          </button>
          <button
            type="button"
            className="h-10 rounded-md bg-[var(--peligro)] px-4 text-sm font-semibold text-white disabled:opacity-60"
            disabled={quitar.isPending || !motivoQuitar.trim()}
            onClick={async () => {
              if (aQuitar) {
                await quitar.mutateAsync({ id: aQuitar.id, motivo: motivoQuitar.trim() });
              }
              setAQuitar(null);
            }}
          >
            Quitar
          </button>
        </div>
      </Modal>
    </section>
  );
}

function BotonSubir({
  enProgreso,
  progreso,
  deshabilitado = false,
  formatos,
  onElegir,
}: {
  enProgreso: boolean;
  progreso: number;
  deshabilitado?: boolean;
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
        disabled={enProgreso || deshabilitado}
      >
        {enProgreso ? (
          <>
            <Clock className="size-4 animate-pulse" aria-hidden="true" />
            {progreso > 0 ? `Subiendo ${progreso}%` : "Subiendo…"}
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
