import { describe, expect, it } from "vitest";
import { colones, porcentaje } from "./formato";

describe("colones", () => {
  it("usa punto de miles y coma decimal, siempre con dos decimales", () => {
    expect(colones("1730870.40")).toBe("¢1.730.870,40");
    expect(colones("1730870.4")).toBe("¢1.730.870,40");
    expect(colones("0")).toBe("¢0,00");
    expect(colones("999")).toBe("¢999,00");
  });
});

describe("porcentaje", () => {
  it("no toca los ceros de un entero", () => {
    expect(porcentaje("10")).toBe("10 %");
    expect(porcentaje("100")).toBe("100 %");
  });

  it("quita los ceros sobrantes después de la coma", () => {
    expect(porcentaje("14.0000")).toBe("14 %");
    expect(porcentaje("0.4760")).toBe("0,476 %");
  });
});
