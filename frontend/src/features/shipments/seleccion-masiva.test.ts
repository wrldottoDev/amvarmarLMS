import { describe, expect, it } from "vitest";
import type { CargaResumen } from "@/lib/api/tipos";
import { alternarCarga, alternarCargasVisibles } from "./seleccion-masiva";

function carga(id: string): CargaResumen {
  return { id } as CargaResumen;
}

describe("selección masiva de cargas", () => {
  it("selecciona y deselecciona una fila", () => {
    const primera = carga("a");
    const seleccionada = alternarCarga(new Map(), primera);
    expect([...seleccionada.keys()]).toEqual(["a"]);
    expect(alternarCarga(seleccionada, primera).size).toBe(0);
  });

  it("selecciona solo las visibles y conserva las de otra página", () => {
    const invisible = carga("anterior");
    const visibles = [carga("a"), carga("b")];
    const seleccionadas = alternarCargasVisibles(new Map([[invisible.id, invisible]]), visibles);
    expect([...seleccionadas.keys()]).toEqual(["anterior", "a", "b"]);

    const sinVisibles = alternarCargasVisibles(seleccionadas, visibles);
    expect([...sinVisibles.keys()]).toEqual(["anterior"]);
  });
});
