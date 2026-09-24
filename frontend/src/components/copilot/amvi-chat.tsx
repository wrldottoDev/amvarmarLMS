"use client";

import { Bot, Loader2, Paperclip, Send, X } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { fragmentarConEnlaces } from "@/features/copilot/enlaces";
import {
  type AdjuntoChat,
  LIMITE_ADJUNTO_BYTES,
  MEDIA_TYPES_ADJUNTO,
  leerComoAdjunto,
  useChatAsistente,
} from "@/features/copilot/usar-chat";
import { useCapacidadesAsistente } from "@/features/copilot/consultas";
import { clases } from "@/lib/utilidades";
import { PropuestaCard } from "./propuesta-card";

const ETIQUETAS_HERRAMIENTA: Record<string, string> = {
  buscar_cargas: "Buscando cargas…",
  consultar_estado_carga: "Consultando la carga…",
  explicar_que_falta: "Revisando requisitos…",
  mis_pendientes: "Revisando tus pendientes…",
  obtener_preferencias: "Leyendo tus preferencias…",
  consultar_despacho: "Consultando el despacho…",
  listar_despachos: "Buscando despachos…",
  como_hago: "Buscando la guía…",
};

function etiquetaHerramienta(nombre: string) {
  return ETIQUETAS_HERRAMIENTA[nombre] ?? "Trabajando…";
}

/** Texto de un mensaje del asistente, con los códigos de carga y despacho
 * que reconozca convertidos en enlaces internos. */
function TextoConEnlaces({ texto }: { texto: string }) {
  return (
    <>
      {fragmentarConEnlaces(texto).map((fragmento, indice) =>
        fragmento.href ? (
          <Link
            key={indice}
            href={fragmento.href}
            className="font-semibold text-[var(--mar)] underline underline-offset-2"
          >
            {fragmento.texto}
          </Link>
        ) : (
          <span key={indice}>{fragmento.texto}</span>
        ),
      )}
    </>
  );
}

function PanelChat({
  nombre,
  puedeAdjuntar,
  onCerrar,
}: {
  nombre: string;
  puedeAdjuntar: boolean;
  onCerrar: () => void;
}) {
  const { mensajes, enviando, herramientasActivas, propuestas, error, enviar, reiniciar } =
    useChatAsistente();
  const [borrador, setBorrador] = useState("");
  const [adjunto, setAdjunto] = useState<AdjuntoChat | null>(null);
  const [errorAdjunto, setErrorAdjunto] = useState<string | null>(null);
  const listaRef = useRef<HTMLDivElement>(null);
  const campoRef = useRef<HTMLTextAreaElement>(null);
  const archivoRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listaRef.current?.scrollTo({ top: listaRef.current.scrollHeight });
  }, [mensajes, herramientasActivas, propuestas]);

  useEffect(() => {
    campoRef.current?.focus();
  }, []);

  useEffect(() => {
    function alTeclado(evento: KeyboardEvent) {
      if (evento.key === "Escape") onCerrar();
    }
    document.addEventListener("keydown", alTeclado);
    return () => document.removeEventListener("keydown", alTeclado);
  }, [onCerrar]);

  function alEnviar(evento: FormEvent) {
    evento.preventDefault();
    const texto = borrador.trim();
    if (!texto || enviando) return;
    const archivo = adjunto ?? undefined;
    setBorrador("");
    setAdjunto(null);
    void enviar(texto, archivo);
  }

  async function alElegirArchivo(archivo: File | undefined) {
    setErrorAdjunto(null);
    if (!archivo) return;
    if (!(MEDIA_TYPES_ADJUNTO as readonly string[]).includes(archivo.type)) {
      setErrorAdjunto("Solo puedo leer PDF o imágenes (JPG, PNG, WEBP).");
      return;
    }
    if (archivo.size > LIMITE_ADJUNTO_BYTES) {
      setErrorAdjunto("El archivo es muy grande: el máximo son 7 MB.");
      return;
    }
    setAdjunto(await leerComoAdjunto(archivo));
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={nombre}
      className="flex h-full w-full flex-col bg-[var(--superficie)] sm:w-[420px] sm:border-l"
    >
      <div className="flex h-[var(--header-h)] shrink-0 items-center justify-between border-b px-4">
        <span className="flex items-center gap-2 font-bold">
          <Bot className="size-5 text-[var(--mar)]" aria-hidden="true" />
          {nombre}
        </span>
        <div className="flex items-center gap-1">
          {mensajes.length > 0 ? (
            <button
              type="button"
              className="rounded-md px-2 py-1 text-xs font-medium text-[var(--texto-secundario)] hover:bg-[var(--hover)]"
              onClick={reiniciar}
            >
              Nueva conversación
            </button>
          ) : null}
          <button
            type="button"
            className="grid size-9 place-items-center rounded-lg hover:bg-[var(--hover)]"
            onClick={onCerrar}
            aria-label="Cerrar asistente"
          >
            <X className="size-5" aria-hidden="true" />
          </button>
        </div>
      </div>

      <div ref={listaRef} className="flex-1 space-y-3 overflow-y-auto p-4">
        {mensajes.length === 0 ? (
          <p className="text-sm text-[var(--texto-secundario)]">
            Preguntame por el estado de una carga, qué documentos faltan, o tus pendientes.
          </p>
        ) : null}

        {mensajes.map((mensaje, indice) => (
          <div key={indice} className="space-y-3">
            <div
              className={clases("flex", mensaje.rol === "user" ? "justify-end" : "justify-start")}
            >
              <div
                className={clases(
                  "max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm",
                  mensaje.rol === "user"
                    ? "bg-[var(--mar)] text-white"
                    : "border bg-[var(--fondo)] text-[var(--texto)]",
                )}
              >
                {mensaje.rol === "assistant" ? (
                  <TextoConEnlaces texto={mensaje.contenido} />
                ) : (
                  mensaje.contenido
                )}
              </div>
            </div>
            {propuestas
              .filter((item) => item.posicion === indice)
              .map((item) => (
                <PropuestaCard key={item.propuesta.id} propuesta={item.propuesta} />
              ))}
          </div>
        ))}

        {herramientasActivas.map((herramienta) => (
          <div key={herramienta.nombre} className="flex items-center gap-2 text-xs text-[var(--texto-secundario)]">
            <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            {etiquetaHerramienta(herramienta.nombre)}
          </div>
        ))}

        {enviando && herramientasActivas.length === 0 ? (
          <div className="flex items-center gap-2 text-xs text-[var(--texto-secundario)]">
            <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            Pensando…
          </div>
        ) : null}

        {error ? (
          <p role="alert" className="rounded-md border border-[var(--peligro)] bg-[var(--peligro-tenue)] px-3 py-2 text-sm text-[var(--peligro)]">
            {error.message}
          </p>
        ) : null}
      </div>

      <form onSubmit={alEnviar} className="shrink-0 border-t p-3">
        {errorAdjunto ? (
          <p className="mb-2 rounded-md bg-[var(--peligro-tenue)] px-2.5 py-1.5 text-xs text-[var(--peligro)]">
            {errorAdjunto}
          </p>
        ) : null}

        {adjunto ? (
          <div className="mb-2 flex items-center gap-2 rounded-md border bg-[var(--hover)] px-2.5 py-1.5 text-xs">
            <Paperclip className="size-3.5 shrink-0 text-[var(--texto-secundario)]" aria-hidden="true" />
            <span className="min-w-0 flex-1 truncate">{adjunto.nombre}</span>
            <button
              type="button"
              onClick={() => setAdjunto(null)}
              className="grid size-5 shrink-0 place-items-center rounded hover:bg-[var(--superficie)]"
              aria-label={`Quitar ${adjunto.nombre}`}
            >
              <X className="size-3.5" aria-hidden="true" />
            </button>
          </div>
        ) : null}

        <div className="flex items-end gap-2">
        {puedeAdjuntar ? (
          <>
            <input
              ref={archivoRef}
              type="file"
              className="hidden"
              accept={MEDIA_TYPES_ADJUNTO.join(",")}
              onChange={(evento) => {
                const archivo = evento.target.files?.[0];
                evento.target.value = "";
                void alElegirArchivo(archivo);
              }}
            />
            <button
              type="button"
              onClick={() => archivoRef.current?.click()}
              disabled={enviando}
              aria-label="Adjuntar una factura o una foto"
              title="Adjuntar una factura o una foto"
              className="grid size-10 shrink-0 place-items-center rounded-md border text-[var(--texto-secundario)] hover:bg-[var(--hover)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Paperclip className="size-4" aria-hidden="true" />
            </button>
          </>
        ) : null}
        <textarea
          ref={campoRef}
          value={borrador}
          onChange={(evento) => setBorrador(evento.target.value)}
          onKeyDown={(evento) => {
            if (evento.key === "Enter" && !evento.shiftKey) {
              evento.preventDefault();
              alEnviar(evento);
            }
          }}
          rows={1}
          maxLength={4000}
          placeholder="Escribí tu pregunta…"
          aria-label="Mensaje para el asistente"
          className="max-h-32 flex-1 resize-none rounded-md border bg-[var(--fondo)] px-3 py-2 text-sm outline-none focus:border-[var(--mar)]"
          disabled={enviando}
        />
        <button
          type="submit"
          disabled={enviando || !borrador.trim()}
          aria-label="Enviar"
          className="grid size-10 shrink-0 place-items-center rounded-md bg-[var(--mar)] text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Send className="size-4" aria-hidden="true" />
        </button>
        </div>
      </form>
    </div>
  );
}

export function AmviChat() {
  const [abierto, setAbierto] = useState(false);
  const { data } = useCapacidadesAsistente();

  if (!data?.disponible) return null;

  return (
    <>
      <button
        type="button"
        className="grid size-10 place-items-center rounded-md text-[var(--texto-secundario)] hover:bg-[var(--hover)]"
        onClick={() => setAbierto(true)}
        aria-label={`Abrir ${data.nombre}`}
        title={data.nombre}
      >
        <Bot className="size-5" aria-hidden="true" />
      </button>

      {abierto ? (
        <div className="fixed inset-0 z-50" role="presentation">
          <div
            className="absolute inset-0 bg-black/40 sm:bg-transparent"
            onMouseDown={() => setAbierto(false)}
          />
          <div className="absolute inset-y-0 right-0" onMouseDown={(evento) => evento.stopPropagation()}>
            <PanelChat
              nombre={data.nombre}
              puedeAdjuntar={data.puede_adjuntar}
              onCerrar={() => setAbierto(false)}
            />
          </div>
        </div>
      ) : null}
    </>
  );
}
