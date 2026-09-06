import { describe, expect, it } from "vitest";
import { aPayload, piezaVacia, problemaDePiezas, totalUnidades } from "./editor-piezas";

/** Una pieza con los campos que interesan al caso, el resto en blanco. */
function pieza(parcial: Partial<ReturnType<typeof piezaVacia>> = {}) {
  return { ...piezaVacia(), ...parcial };
}

describe("totalUnidades", () => {
  it("suma las cantidades, no cuenta las filas", () => {
    // Tres pallets y dos tambores son cinco bultos, no dos tipos.
    const total = totalUnidades([pieza({ quantity: "3" }), pieza({ quantity: "2" })]);
    expect(total).toBe(5);
  });

  it("trata un campo vacío como cero mientras se escribe", () => {
    expect(totalUnidades([pieza({ quantity: "" })])).toBe(0);
  });
});

describe("problemaDePiezas", () => {
  it("exige al menos una pieza", () => {
    expect(problemaDePiezas([])).toMatch(/al menos una/i);
  });

  it("acepta el desglose mínimo", () => {
    expect(problemaDePiezas([pieza()])).toBeNull();
  });

  it("rechaza cantidad cero y dice cuál fila", () => {
    const problema = problemaDePiezas([pieza(), pieza({ quantity: "0" })]);
    expect(problema).toMatch(/pieza 2/);
  });

  it("rechaza cantidad fraccionaria", () => {
    // Medio pallet no existe como unidad de conteo.
    expect(problemaDePiezas([pieza({ quantity: "1.5" })])).not.toBeNull();
  });

  it("acepta peso y dimensiones en blanco porque son opcionales", () => {
    expect(problemaDePiezas([pieza({ weight_kg: "", length_cm: "" })])).toBeNull();
  });

  it("rechaza una dimensión en cero", () => {
    // Un bulto de cero centímetros es un dato mal capturado, no un bulto.
    expect(problemaDePiezas([pieza({ length_cm: "0" })])).toMatch(/largo/i);
  });
});

describe("aPayload", () => {
  it("manda los opcionales vacíos como null, no como cero", () => {
    const [enviado] = aPayload([pieza({ quantity: "2", weight_kg: "", description: "  " })]);

    expect(enviado.quantity).toBe(2);
    expect(enviado.weight_kg).toBeNull();
    expect(enviado.description).toBeNull();
  });

  it("conserva los valores informados", () => {
    const [enviado] = aPayload([
      pieza({ package_type: "DRUM", quantity: "4", weight_kg: "820.5", description: " Repuestos " }),
    ]);

    expect(enviado.package_type).toBe("DRUM");
    expect(enviado.weight_kg).toBe("820.5");
    expect(enviado.description).toBe("Repuestos");
  });
});
