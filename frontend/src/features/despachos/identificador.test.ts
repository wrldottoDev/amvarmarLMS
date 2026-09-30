import { describe, expect, it } from "vitest";
import { identificadorDespacho } from "./identificador";

describe("identificadorDespacho", () => {
  it("muestra la referencia comercial de una carga", () => {
    expect(identificadorDespacho(["WR-1234"])).toBe("WR-1234");
  });

  it("muestra todos los WR o facturas cuando el despacho agrupa cargas", () => {
    expect(identificadorDespacho(["WR-1234", "FAC-9876"])).toBe("WR-1234 · FAC-9876");
  });

  it("no vuelve a usar el número DSP como sustituto", () => {
    expect(identificadorDespacho([])).toBe("Sin WR, BL o factura");
  });
});
