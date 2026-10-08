"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Check, PowerOff, ThumbsDown } from "lucide-react";
import { useState } from "react";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { AreaTexto, Campo } from "@/components/ui/campo";
import { CargandoPagina, EstadoVacio } from "@/components/ui/estados-pagina";
import { api, exigirDatos } from "@/lib/api/client";
import { formatearFecha } from "@/lib/utilidades";

const clave = ["copilot", "aprendizaje"] as const;

/**
 * Cómo aprende AMVI (ADR-0012, enmienda 2026-10-08): lo que los usuarios
 * calificaron mal y lo que no supo contestar llega acá; Operaciones escribe la
 * guía que falta y AMVI la usa desde ese momento. Una persona decide qué es
 * correcto: AMVI no se reentrena sola.
 */
export default function PaginaAprendizajeAmvi() {
  const cliente = useQueryClient();
  const consulta = useQuery({
    queryKey: clave,
    queryFn: async () => exigirDatos(await api.GET("/api/v1/copilot/learning")),
  });
  const [titulo, setTitulo] = useState("");
  const [palabras, setPalabras] = useState("");
  const [contenido, setContenido] = useState("");
  const [temaOrigen, setTemaOrigen] = useState<string | null>(null);

  const refrescar = () => cliente.invalidateQueries({ queryKey: clave });
  const crear = useMutation({
    mutationFn: async () => {
      exigirDatos(
        await api.POST("/api/v1/copilot/guides", {
          body: { titulo, palabras_clave: palabras, contenido },
        }),
      );
      if (temaOrigen) {
        await api.POST("/api/v1/copilot/unanswered/{topic_id}/resolve", {
          params: { path: { topic_id: temaOrigen } },
        });
      }
    },
    onSuccess: () => {
      setTitulo("");
      setPalabras("");
      setContenido("");
      setTemaOrigen(null);
      void refrescar();
    },
  });
  const desactivar = useMutation({
    mutationFn: async (id: string) =>
      api.POST("/api/v1/copilot/guides/{guide_id}/deactivate", {
        params: { path: { guide_id: id } },
      }),
    onSuccess: () => void refrescar(),
  });
  const resolver = useMutation({
    mutationFn: async (id: string) =>
      api.POST("/api/v1/copilot/unanswered/{topic_id}/resolve", {
        params: { path: { topic_id: id } },
      }),
    onSuccess: () => void refrescar(),
  });

  function escribirGuia(pregunta: string, tema?: string) {
    setTitulo(pregunta.slice(0, 160));
    setPalabras(pregunta.toLowerCase().replace(/[¿?¡!.,]/g, "").slice(0, 500));
    setContenido("");
    setTemaOrigen(tema ?? null);
    document.getElementById("nueva-guia")?.scrollIntoView({ behavior: "smooth" });
  }

  if (consulta.isPending) return <CargandoPagina texto="Cargando aprendizaje" />;
  if (consulta.error) return <AvisoError error={consulta.error} />;
  const datos = consulta.data;

  return (
    <section className="mx-auto max-w-4xl space-y-6">
      <header>
        <p className="text-xs font-bold uppercase text-[var(--marca)]">AMVI</p>
        <h1 className="mt-1 text-2xl font-bold">Aprendizaje</h1>
        <p className="mt-1 text-sm text-[var(--texto-secundario)]">
          Lo que AMVI no supo contestar o contestó mal. Escribí la guía que falta y AMVI la usa desde
          ese momento.
        </p>
      </header>

      <div className="space-y-2">
        <h2 className="flex items-center gap-2 font-bold">
          <ThumbsDown className="size-4" aria-hidden="true" /> Respuestas que no sirvieron
        </h2>
        {datos.mal_calificadas.length === 0 ? (
          <EstadoVacio titulo="Nada por revisar" descripcion="Nadie calificó mal una respuesta." />
        ) : (
          <ul className="divide-y rounded-lg border bg-[var(--superficie)]">
            {datos.mal_calificadas.map((item) => (
              <li key={String(item.id)} className="space-y-1.5 px-4 py-3 text-sm">
                <p>
                  <strong>Pregunta:</strong> {String(item.pregunta ?? "—")}
                </p>
                <p className="text-[var(--texto-secundario)]">
                  <strong>AMVI:</strong> {String(item.respuesta)}
                </p>
                {item.comentario ? (
                  <p className="text-[var(--advertencia)]">
                    <strong>Comentario:</strong> {String(item.comentario)}
                  </p>
                ) : null}
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs text-[var(--texto-secundario)]">
                    {formatearFecha(String(item.created_at))}
                  </span>
                  <Boton variante="secundario" onClick={() => escribirGuia(String(item.pregunta ?? ""))}>
                    Escribir guía
                  </Boton>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-2">
        <h2 className="flex items-center gap-2 font-bold">
          <BookOpen className="size-4" aria-hidden="true" /> Temas sin guía
        </h2>
        {datos.temas_sin_guia.length === 0 ? (
          <EstadoVacio titulo="Sin temas pendientes" descripcion="AMVI encontró guía para todo." />
        ) : (
          <ul className="divide-y rounded-lg border bg-[var(--superficie)]">
            {datos.temas_sin_guia.map((tema) => (
              <li key={String(tema.id)} className="flex flex-wrap items-center gap-2 px-4 py-3 text-sm">
                <span className="min-w-0 flex-1">{String(tema.topic)}</span>
                <Boton variante="secundario" onClick={() => escribirGuia(String(tema.topic), String(tema.id))}>
                  Escribir guía
                </Boton>
                <button
                  type="button"
                  className="flex h-9 items-center gap-1 rounded-md px-2 text-xs text-[var(--texto-secundario)] hover:bg-[var(--hover)]"
                  onClick={() => resolver.mutate(String(tema.id))}
                  title="No hace falta una guía para esto"
                >
                  <Check className="size-3.5" aria-hidden="true" /> Descartar
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <form
        id="nueva-guia"
        className="space-y-3 rounded-lg border bg-[var(--superficie)] p-4"
        onSubmit={(evento) => {
          evento.preventDefault();
          crear.mutate();
        }}
      >
        <h2 className="font-bold">Nueva guía</h2>
        <Campo etiqueta="Título" value={titulo} maxLength={160} onChange={(e) => setTitulo(e.target.value)} />
        <Campo
          etiqueta="Palabras clave"
          ayuda="Separadas por coma: cómo lo preguntaría alguien. Ej.: horario, atención, bodega."
          value={palabras}
          maxLength={500}
          onChange={(e) => setPalabras(e.target.value)}
        />
        <AreaTexto
          etiqueta="Respuesta"
          rows={5}
          value={contenido}
          maxLength={8000}
          onChange={(e) => setContenido(e.target.value)}
        />
        {crear.error ? <AvisoError error={crear.error} /> : null}
        <div className="flex justify-end">
          <Boton
            type="submit"
            cargando={crear.isPending}
            disabled={titulo.trim().length < 3 || palabras.trim().length < 2 || contenido.trim().length < 10}
          >
            Guardar guía
          </Boton>
        </div>
      </form>

      {datos.guias.length > 0 ? (
        <div className="space-y-2">
          <h2 className="font-bold">Guías escritas por Operaciones</h2>
          <ul className="divide-y rounded-lg border bg-[var(--superficie)]">
            {datos.guias.map((guia) => (
              <li key={String(guia.id)} className="flex items-start gap-3 px-4 py-3 text-sm">
                <span className="min-w-0 flex-1">
                  <strong className="block">{String(guia.title)}</strong>
                  <span className="block text-xs text-[var(--texto-secundario)]">{String(guia.keywords)}</span>
                </span>
                <button
                  type="button"
                  className="flex h-9 items-center gap-1 rounded-md px-2 text-xs text-[var(--peligro)] hover:bg-[var(--peligro-tenue)]"
                  onClick={() => desactivar.mutate(String(guia.id))}
                >
                  <PowerOff className="size-3.5" aria-hidden="true" /> Desactivar
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
