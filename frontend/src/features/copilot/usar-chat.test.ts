import { describe, expect, it } from "vitest";
import { leerCampoSSE } from "./usar-chat";

describe("leerCampoSSE", () => {
  it("separa el nombre de evento del cuerpo JSON", () => {
    expect(leerCampoSSE('event: token\ndata: {"texto":"Hola"}')).toEqual({
      evento: "token",
      datos: { texto: "Hola" },
    });
  });

  it("interpreta un evento de herramienta ejecutándose", () => {
    expect(
      leerCampoSSE('event: herramienta\ndata: {"nombre":"buscar_cargas","estado":"ejecutando"}'),
    ).toEqual({
      evento: "herramienta",
      datos: { nombre: "buscar_cargas", estado: "ejecutando" },
    });
  });

  it("interpreta un evento de error", () => {
    expect(
      leerCampoSSE('event: error\ndata: {"code":"COPILOT_NO_DISPONIBLE","message":"no"}'),
    ).toEqual({
      evento: "error",
      datos: { code: "COPILOT_NO_DISPONIBLE", message: "no" },
    });
  });

  it("interpreta un evento de propuesta con la estructura completa", () => {
    const datos =
      '{"id":"p1","action_code":"crear_prealerta_borrador","titulo":"Borrador",' +
      '"resumen_efecto":"Crea una carga.","expira_en":"2026-01-01T00:00:00Z",' +
      '"campos":[{"nombre":"peso_kg","etiqueta":"Peso (kg)","valor":10,"confianza":1,"editable":true}],' +
      '"advertencias":[]}';
    expect(leerCampoSSE(`event: propuesta\ndata: ${datos}`)).toEqual({
      evento: "propuesta",
      datos: JSON.parse(datos),
    });
  });

  it("devuelve null cuando el bloque no trae datos", () => {
    expect(leerCampoSSE("event: fin")).toBeNull();
  });

  it("reconstruye datos partidos en varias líneas `data:`", () => {
    // El formateador del backend siempre emite una sola línea `data:`, pero el
    // formato SSE lo permite partido — el parser no debe asumir una sola línea.
    expect(leerCampoSSE('event: token\ndata: {"texto":\ndata: "hola"}')).toEqual({
      evento: "token",
      datos: { texto: "hola" },
    });
  });
});
