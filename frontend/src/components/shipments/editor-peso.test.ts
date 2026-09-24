import { describe, expect, it } from "vitest";
import {
  cambiarPeso,
  normalizarPeso,
  pesoParaApi,
  pesoValido,
  pesoVacio,
} from "./editor-peso";

describe("EditorPeso", () => {
  it("convierte kilos a libras y conserva KG como fuente", () => {
    expect(cambiarPeso(pesoVacio, "KG", "10")).toEqual({
      kg: "10",
      lb: "22.046",
      unidadFuente: "KG",
    });
  });

  it("convierte libras a kilos y conserva LB como fuente", () => {
    expect(cambiarPeso(pesoVacio, "LB", "22.046")).toEqual({
      kg: "10.000",
      lb: "22.046",
      unidadFuente: "LB",
    });
  });

  it("normaliza la fuente al salir del campo", () => {
    expect(normalizarPeso(cambiarPeso(pesoVacio, "KG", "1.2"))).toEqual({
      kg: "1.200",
      lb: "2.646",
      unidadFuente: "KG",
    });
  });

  it("rechaza vacío, cero, negativos e infinito", () => {
    for (const valor of ["", "0", "-2", "1e309"]) {
      expect(pesoValido(cambiarPeso(pesoVacio, "KG", valor))).toBe(false);
    }
  });

  it("envía una sola magnitud con su unidad fuente", () => {
    expect(pesoParaApi(cambiarPeso(pesoVacio, "LB", "100"))).toEqual({
      value: "100",
      unit: "LB",
    });
  });
});
