import { describe, expect, it } from "vitest";
import { identificadorCarga } from "./identificador";

describe("identificadorCarga", () => {
  it("muestra WR como principal para Miami", () => {
    expect(identificadorCarga({ wr: "105921", bl: "BL-2", invoice: "F-1" })).toBe(
      "WR 105921",
    );
  });

  it("muestra BL como principal para tránsito", () => {
    expect(identificadorCarga({ bl: "MSC-7788", invoice: "F-2" })).toBe("BL MSC-7788");
  });

  it("conserva la factura como respaldo", () => {
    expect(identificadorCarga({ invoice: "F-3" })).toBe("Factura F-3");
  });
});
