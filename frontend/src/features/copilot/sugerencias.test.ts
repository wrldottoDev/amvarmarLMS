import { describe, expect, it } from "vitest";
import { sugerenciasPara } from "./sugerencias";

describe("sugerencias de AMVI por pantalla", () => {
  it("en el inventario de Miami el cliente ve qué puede despachar", () => {
    expect(sugerenciasPara("/miami", true)).toContain("¿Qué cargas puedo despachar?");
  });

  it("gana el prefijo más largo", () => {
    expect(sugerenciasPara("/shipments/historial", true)[0]).toBe(
      "¿Dónde están mis cargas entregadas?",
    );
    expect(sugerenciasPara("/shipments/abc", true)[0]).toBe("¿Qué le falta a esta carga?");
  });

  it("fuera de una sección conocida ofrece las generales", () => {
    expect(sugerenciasPara("/cuenta", false)).toEqual([
      "¿Qué tengo pendiente hoy?",
      "¿Qué cargas llegan esta semana?",
      "¿Cómo registro una carga?",
    ]);
  });
});
