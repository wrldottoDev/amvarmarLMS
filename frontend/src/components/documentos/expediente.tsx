"use client";

import { AlertTriangle, Check, Clock, Download, FileText, Pencil, Trash2, Upload, UploadCloud } from "lucide-react";
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

type ArchivoEnLote = {
  clave: string;
  nombre: string;
  estado: "pendiente" | "subiendo" | "listo" | "error";
  progreso: number;
  error?: string;
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
  soloParaSolicitarDespacho = false,
}: {
  cargaId: string;
  esCliente: boolean;
  /** Historial de despachos (ADR-0007): una carga archivada no admite
   * documentos nuevos, renombres ni bajas — el backend lo rechaza igual. */
  soloLectura?: boolean;
  /** En la solicitud muestra únicamente lo que el cliente debe aportar antes de enviarla. */
  soloParaSolicitarDespacho?: boolean;
}) {
  const { data, isPending, error } = useExpediente(cargaId);
  const subir = useSubirDocumento(cargaId);
  const descargar = useDescargar();
  const renombrar = useRenombrarDocumento(cargaId);
  const quitar = useQuitarDocumento(cargaId);

  const [subiendo, setSubiendo] = useState<string | null>(null);
  // Lo que eligió y todavía no confirmó: subir pide un paso de confirmación,
  // pero ninguna aprobación posterior (pedido de AMVARMAR, 2026-10-08).
  const [porConfirmar, setPorConfirmar] = useState<{
    archivos: File[];
    tipoId: string;
    etiqueta: string;
    issuedBy: IssuedBy;
    clave: string;
  } | null>(null);
  const [emisores, setEmisores] = useState<Record<string, IssuedBy>>({});
  const [tipoLibre, setTipoLibre] = useState("");
  const [emisorLibre, setEmisorLibre] = useState<IssuedBy | "">("");
  const [renombrando, setRenombrando] = useState<string | null>(null);
  const [nombreNuevo, setNombreNuevo] = useState("");
  const [aQuitar, setAQuitar] = useState<{ id: string; original_name: string } | null>(null);
  const [motivoQuitar, setMotivoQuitar] = useState("");
  const [arrastrando, setArrastrando] = useState(false);
  const [lote, setLote] = useState<ArchivoEnLote[]>([]);
  const entradaLote = useRef<HTMLInputElement>(null);

  // Renombrar y quitar son de personal interno, igual que `edit_files` del
  // sistema viejo, que estaba bajo `@staff_member_required`.
  const esInterno = !esCliente;

  if (isPending) return null;
  if (error) return <AvisoError error={error} />;
  if (!data) return null;

  // Al cliente solo se le piden los documentos que le tocan a él. Un packing
  // list lo carga Operaciones, y mostrárselo como pendiente suyo lo haría
  // buscar un archivo que nunca tuvo (ADR-0003).
  const requisitosDelActor = esCliente
    ? data.requisitos.filter((r) => r.required_from === "CLIENT")
    : data.requisitos;
  const requisitos = soloParaSolicitarDespacho
    ? requisitosDelActor.filter((r) => r.required_before_status === "DISPATCHED")
    : requisitosDelActor;

  const faltantes = requisitos.filter(
    (r) =>
      r.required_before_status === "DISPATCHED" &&
      ["PENDING", "REJECTED", "OPEN"].includes(r.status),
  );

  async function guardarNombre(documentoId: string) {
    await renombrar.mutateAsync({ id: documentoId, nombre: nombreNuevo.trim() });
    setRenombrando(null);
  }

  function alElegirArchivo(
    requisito: RequisitoDocumental,
    archivo: File | undefined,
    issuedBy: IssuedBy,
  ) {
    if (!archivo || !requisito.document_type_id) return;
    setPorConfirmar({
      archivos: [archivo],
      tipoId: requisito.document_type_id,
      etiqueta: requisito.label,
      issuedBy,
      clave: requisito.id,
    });
  }

  async function abrir(documentoId: string) {
    const enlace = await descargar.mutateAsync(documentoId);
    // Pestaña nueva: si el usuario vuelve atrás no pierde la página de la carga.
    window.open(enlace.url, "_blank", "noopener,noreferrer");
  }

  const tipoLibreSeleccionado = data.tipos.find((tipo) => tipo.id === tipoLibre);
  const opcionesEmisorLibre = (tipoLibreSeleccionado?.issued_by_options ?? []) as IssuedBy[];
  const emisorLibreEfectivo = emisorLibre || opcionesEmisorLibre[0];

  function elegirLibres(archivos: File[]) {
    if (!tipoLibreSeleccionado || !emisorLibreEfectivo || archivos.length === 0) return;
    setPorConfirmar({
      archivos,
      tipoId: tipoLibreSeleccionado.id,
      etiqueta: tipoLibreSeleccionado.label,
      issuedBy: emisorLibreEfectivo,
      clave: "libre",
    });
  }

  async function subirConfirmados() {
    if (!porConfirmar) return;
    const { archivos, tipoId, issuedBy, clave: origen } = porConfirmar;
    setPorConfirmar(null);
    const pendientes: ArchivoEnLote[] = archivos.map((archivo, indice) => ({
      clave: `${archivo.name}-${archivo.size}-${archivo.lastModified}-${indice}`,
      nombre: archivo.name,
      estado: "pendiente",
      progreso: 0,
    }));
    setLote(pendientes);
    setSubiendo(origen);

    for (let indice = 0; indice < archivos.length; indice += 1) {
      const archivo = archivos[indice];
      const clave = pendientes[indice].clave;
      const actualizar = (cambio: Partial<ArchivoEnLote>) =>
        setLote((actual) =>
          actual.map((item) => (item.clave === clave ? { ...item, ...cambio } : item)),
        );
      actualizar({ estado: "subiendo", progreso: 0 });
      try {
        await subir.mutateAsync({
          archivo,
          tipoId,
          issuedBy,
          onProgress: (valor) => actualizar({ progreso: valor }),
        });
        actualizar({ estado: "listo", progreso: 100 });
      } catch (error) {
        actualizar({
          estado: "error",
          error: error instanceof Error ? error.message : "No se pudo subir.",
        });
      }
    }
    setSubiendo(null);
  }

  const progresoActual = lote.find((item) => item.estado === "subiendo")?.progreso ?? 0;

  return (
    <section aria-labelledby={`documentos-carga-${cargaId}`} className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <FileText className="size-5 text-[var(--marca)]" aria-hidden="true" />
          <h2 id={`documentos-carga-${cargaId}`} className="text-base font-bold">
            {soloParaSolicitarDespacho
              ? "Documentos opcionales para el despacho"
              : "Documentos"}
          </h2>
        </div>

        {!soloParaSolicitarDespacho && data.documentos.length > 1 ? (
          <ExportacionCarga cargaId={cargaId} />
        ) : null}
      </div>

      {esCliente ? (
        <p
          className={clases(
            "rounded-md border px-4 py-3 text-sm",
            faltantes.length > 0 ? tonos.falta : tonos.listo,
          )}
        >
          {faltantes.length === 0
            ? "Ya adjuntaste todos los documentos sugeridos."
            : faltantes.length === 1
              ? "Hay 1 documento sugerido pendiente. Podés continuar sin subirlo."
              : `Hay ${faltantes.length} documentos sugeridos pendientes. Podés continuar sin subirlos.`}
        </p>
      ) : null}

      {subir.error ? <AvisoError error={subir.error} /> : null}
      {descargar.error ? <AvisoError error={descargar.error} /> : null}

      {lote.length > 0 ? (
        <ul className="space-y-1" aria-label="Resultado de la carga de archivos">
          {lote.map((archivo) => (
            <li key={archivo.clave} className="flex items-center gap-2 text-xs">
              {archivo.estado === "listo" ? (
                <Check className="size-3.5 text-[var(--exito)]" aria-hidden="true" />
              ) : archivo.estado === "error" ? (
                <AlertTriangle className="size-3.5 text-[var(--peligro)]" aria-hidden="true" />
              ) : (
                <Clock className="size-3.5 text-[var(--texto-secundario)]" aria-hidden="true" />
              )}
              <span className="truncate font-medium">{archivo.nombre}</span>
              <span className="text-[var(--texto-secundario)]">
                {archivo.estado === "subiendo"
                  ? `Subiendo ${archivo.progreso}%`
                  : archivo.estado === "listo"
                    ? "Subido y disponible"
                    : archivo.estado === "error"
                      ? archivo.error
                      : "En espera"}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

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
                      progreso={enProgreso ? progresoActual : 0}
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
          {soloParaSolicitarDespacho
            ? "No tenés documentos pendientes para solicitar el despacho."
            : "Esta carga no tiene documentos pendientes."}
        </p>
      )}

      {data.tipos.length > 0 && !soloLectura && !soloParaSolicitarDespacho ? (
        <div className="space-y-3 border-y bg-[var(--superficie)] px-4 py-3">
          <div className="flex flex-wrap items-end gap-2">
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
            progreso={subiendo === "libre" ? progresoActual : 0}
            formatos={tipoLibreSeleccionado?.allowed_formats ?? []}
            onElegir={(archivo) => elegirLibres(archivo ? [archivo] : [])}
            deshabilitado={!tipoLibreSeleccionado || !emisorLibreEfectivo}
          />
          </div>

          <input
            ref={entradaLote}
            type="file"
            multiple
            className="hidden"
            accept={(tipoLibreSeleccionado?.allowed_formats ?? [])
              .map((formato) => `.${formato.toLowerCase()}`)
              .join(",")}
            onChange={(evento) => {
              elegirLibres(Array.from(evento.target.files ?? []));
              evento.target.value = "";
            }}
          />
          <button
            type="button"
            className={clases(
              "flex w-full flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-7 text-center transition-colors",
              arrastrando ? "border-[var(--marca)] bg-[var(--marca-tenue)]" : "hover:bg-[var(--hover)]",
              (!tipoLibreSeleccionado || !emisorLibreEfectivo || subiendo === "lote") &&
                "cursor-not-allowed opacity-60",
            )}
            disabled={!tipoLibreSeleccionado || !emisorLibreEfectivo || subiendo === "lote"}
            onClick={() => entradaLote.current?.click()}
            onDragEnter={(evento) => {
              evento.preventDefault();
              setArrastrando(true);
            }}
            onDragOver={(evento) => evento.preventDefault()}
            onDragLeave={() => setArrastrando(false)}
            onDrop={(evento) => {
              evento.preventDefault();
              setArrastrando(false);
              elegirLibres(Array.from(evento.dataTransfer.files));
            }}
          >
            <UploadCloud className="mb-2 size-7 text-[var(--marca)]" aria-hidden="true" />
            <strong className="text-sm">Arrastrá varios archivos aquí</strong>
            <span className="mt-1 text-xs text-[var(--texto-secundario)]">
              Primero seleccioná el tipo documental. También podés hacer clic para elegirlos.
            </span>
          </button>

        </div>
      ) : null}

      {!soloParaSolicitarDespacho && data.documentos.length > 0 ? (
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

      {/* Confirmación explícita: quitar un documento reabre su recomendación y
          elimina el acceso normal al archivo dentro del expediente. */}
      <Modal
        abierto={aQuitar !== null}
        titulo="¿Quitar este documento del expediente?"
        cerrar={() => setAQuitar(null)}
      >
        <p className="text-sm">
          <strong>{aQuitar?.original_name}</strong> deja de aparecer en el expediente. Si era el
          único de su tipo, la recomendación vuelve a quedar pendiente. Esto no impide cambiar el
          estado de la carga ni procesar su despacho.
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

      {/* Confirmación antes de subir: es el único paso. Después no hay
          aprobación — el archivo queda en el expediente al instante. */}
      <Modal
        abierto={porConfirmar !== null}
        titulo={
          porConfirmar && porConfirmar.archivos.length > 1
            ? `¿Subir ${porConfirmar.archivos.length} archivos?`
            : "¿Subir este archivo?"
        }
        cerrar={() => setPorConfirmar(null)}
      >
        <p className="text-sm">
          Se {porConfirmar && porConfirmar.archivos.length > 1 ? "suben" : "sube"} como{" "}
          <strong>{porConfirmar?.etiqueta}</strong>
          {porConfirmar ? ` (emitido por ${etiquetaEmisor[porConfirmar.issuedBy].toLowerCase()})` : ""}.
        </p>
        <ul className="mt-3 space-y-1 rounded-md border bg-[var(--hover)] px-3 py-2 text-sm">
          {porConfirmar?.archivos.map((archivo, indice) => (
            <li key={`${archivo.name}-${indice}`} className="flex justify-between gap-3">
              <span className="truncate font-medium">{archivo.name}</span>
              <span className="shrink-0 text-[var(--texto-secundario)]">
                {formatearTamano(archivo.size)}
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-[var(--texto-secundario)]">
          Queda disponible en el expediente apenas termina de subir: nadie tiene que aprobarlo.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            className="h-10 rounded-md border px-4 text-sm font-medium hover:bg-[var(--hover)]"
            onClick={() => setPorConfirmar(null)}
          >
            Cancelar
          </button>
          <button
            type="button"
            className="flex h-10 items-center gap-2 rounded-md bg-[var(--mar)] px-4 text-sm font-semibold text-white"
            onClick={() => void subirConfirmados()}
          >
            <Upload className="size-4" aria-hidden="true" />
            Subir
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
