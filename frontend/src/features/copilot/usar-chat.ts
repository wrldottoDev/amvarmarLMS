"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchAutenticado } from "@/lib/api/client";

// `openapi-fetch` no transmite un cuerpo en streaming (ADR-0012); este hook
// habla SSE directo contra el mismo backend que usa el resto de la API.
const baseUrl = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/$/, "");

export interface MensajeConversacion {
  rol: "user" | "assistant";
  contenido: string;
}

export interface EstadoHerramienta {
  nombre: string;
  estado: "ejecutando" | "completada";
}

export interface ErrorAsistente {
  code: string;
  message: string;
}

/** Espejo de `CampoPropuesto` (backend, ADR-0012): un campo que AMVI extrajo
 * o infirió, con qué tan seguro está. */
export interface CampoPropuesto {
  nombre: string;
  etiqueta: string;
  valor: string | number | null;
  confianza: number;
  editable: boolean;
}

/** Espejo de `PropuestaAccion` (backend): lo que devuelve una herramienta de
 * escritura. Nunca tocó la base — la persona la revisa y confirma aparte. */
export interface PropuestaAccion {
  id: string;
  action_code: string;
  titulo: string;
  resumen_efecto: string;
  expira_en: string;
  campos: CampoPropuesto[];
  advertencias: string[];
}

/** Una propuesta anclada a la posición del mensaje del usuario que la
 * originó, para poder dibujarla en el lugar correcto de la conversación. */
export interface PropuestaEnConversacion {
  posicion: number;
  propuesta: PropuestaAccion;
}

export function leerCampoSSE(
  bloque: string,
): { evento: string; datos: Record<string, unknown> } | null {
  let evento = "message";
  let datosCrudos = "";
  for (const linea of bloque.split("\n")) {
    if (linea.startsWith("event: ")) evento = linea.slice("event: ".length);
    else if (linea.startsWith("data: ")) datosCrudos += linea.slice("data: ".length);
  }
  if (!datosCrudos) return null;
  return { evento, datos: JSON.parse(datosCrudos) as Record<string, unknown> };
}

/**
 * El chat no se persiste (ADR-0012): `mensajes` vive solo en este hook, y se
 * pierde al cerrar el panel o recargar. Cada turno reenvía la conversación
 * completa porque el backend tampoco la retiene (`store=false`).
 */
export function useChatAsistente() {
  const [mensajes, setMensajes] = useState<MensajeConversacion[]>([]);
  const [enviando, setEnviando] = useState(false);
  const [herramientasActivas, setHerramientasActivas] = useState<EstadoHerramienta[]>([]);
  const [propuestas, setPropuestas] = useState<PropuestaEnConversacion[]>([]);
  const [error, setError] = useState<ErrorAsistente | null>(null);
  const controladorRef = useRef<AbortController | null>(null);

  useEffect(() => () => controladorRef.current?.abort(), []);

  const enviar = useCallback(
    async (texto: string) => {
      const historial = [...mensajes, { rol: "user" as const, contenido: texto }];
      const posicionAncla = historial.length - 1;
      setMensajes(historial);
      setError(null);
      setHerramientasActivas([]);
      setEnviando(true);

      const controlador = new AbortController();
      controladorRef.current = controlador;
      let textoRespuesta = "";

      try {
        const respuesta = await fetchAutenticado(`${baseUrl}/api/v1/copilot/respond`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mensajes: historial }),
          signal: controlador.signal,
        });

        if (!respuesta.ok || !respuesta.body) {
          setError({ code: "COPILOT_ERROR_RED", message: "No se pudo contactar al asistente." });
          return;
        }

        const lector = respuesta.body.getReader();
        const decodificador = new TextDecoder();
        let buffer = "";

        for (;;) {
          const { done, value } = await lector.read();
          if (done) break;
          buffer += decodificador.decode(value, { stream: true });

          let indice = buffer.indexOf("\n\n");
          while (indice !== -1) {
            const campo = leerCampoSSE(buffer.slice(0, indice));
            buffer = buffer.slice(indice + 2);
            indice = buffer.indexOf("\n\n");
            if (!campo) continue;

            if (campo.evento === "token") {
              textoRespuesta += campo.datos.texto as string;
            } else if (campo.evento === "herramienta") {
              const nombre = campo.datos.nombre as string;
              const estadoHerramienta = campo.datos.estado as EstadoHerramienta["estado"];
              setHerramientasActivas((previas) => {
                const otras = previas.filter((h) => h.nombre !== nombre);
                return estadoHerramienta === "completada"
                  ? otras
                  : [...otras, { nombre, estado: estadoHerramienta }];
              });
            } else if (campo.evento === "propuesta") {
              const propuesta = campo.datos as unknown as PropuestaAccion;
              setPropuestas((previas) => [...previas, { posicion: posicionAncla, propuesta }]);
            } else if (campo.evento === "error") {
              setError({ code: campo.datos.code as string, message: campo.datos.message as string });
            }
          }
        }
      } catch (excepcion) {
        if ((excepcion as Error).name !== "AbortError") {
          setError({
            code: "COPILOT_ERROR_RED",
            message: "Se perdió la conexión con el asistente.",
          });
        }
      } finally {
        setEnviando(false);
        setHerramientasActivas([]);
        controladorRef.current = null;
        if (textoRespuesta) {
          setMensajes((previos) => [...previos, { rol: "assistant", contenido: textoRespuesta }]);
        }
      }
    },
    [mensajes],
  );

  const cancelar = useCallback(() => controladorRef.current?.abort(), []);

  const reiniciar = useCallback(() => {
    controladorRef.current?.abort();
    setMensajes([]);
    setHerramientasActivas([]);
    setPropuestas([]);
    setError(null);
    setEnviando(false);
  }, []);

  return {
    mensajes,
    enviando,
    herramientasActivas,
    propuestas,
    error,
    enviar,
    cancelar,
    reiniciar,
  };
}
