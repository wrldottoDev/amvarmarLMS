import { describe, expect, it } from "vitest";
import { fragmentarConEnlaces } from "./enlaces";

describe("fragmentarConEnlaces", () => {
  it("enlaza un código de carga al listado filtrado", () => {
    const fragmentos = fragmentarConEnlaces("La carga SHP-2026-000123 está en bodega.");

    expect(fragmentos).toEqual([
      { texto: "La carga " },
      { texto: "SHP-2026-000123", href: "/shipments?q=SHP-2026-000123" },
      { texto: " está en bodega." },
    ]);
  });

  it("enlaza un código de despacho al listado general", () => {
    const fragmentos = fragmentarConEnlaces("Revisá DSP-2026-000045.");

    expect(fragmentos).toEqual([
      { texto: "Revisá " },
      { texto: "DSP-2026-000045", href: "/despachos" },
      { texto: "." },
    ]);
  });

  it("devuelve el texto entero sin enlaces cuando no hay coincidencias", () => {
    expect(fragmentarConEnlaces("No encontré ninguna carga con ese dato.")).toEqual([
      { texto: "No encontré ninguna carga con ese dato." },
    ]);
  });

  it("enlaza varios códigos en el mismo mensaje", () => {
    const fragmentos = fragmentarConEnlaces("SHP-2026-000001 y SHP-2026-000002 llegan mañana.");

    expect(fragmentos).toEqual([
      { texto: "SHP-2026-000001", href: "/shipments?q=SHP-2026-000001" },
      { texto: " y " },
      { texto: "SHP-2026-000002", href: "/shipments?q=SHP-2026-000002" },
      { texto: " llegan mañana." },
    ]);
  });
});
