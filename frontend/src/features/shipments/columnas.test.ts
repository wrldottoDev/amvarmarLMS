import { describe, expect, it } from "vitest";
import { columnaPermitida, columnasParaListado } from "./columnas";

describe("columnas de reportes de tránsito para clientes", () => {
  it.each(["carrier", "tracking", "po", "container", "invoice"])(
    "oculta %s permanentemente",
    (columna) => {
      expect(columnaPermitida(columna, true, "TRANSIT")).toBe(false);
    },
  );

  it("reemplaza Fecha por ETA y asegura BL y proveedor", () => {
    expect(
      columnasParaListado(
        ["identificador", "estado", "fecha", "carrier", "pendientes"],
        true,
        "TRANSIT",
      ),
    ).toEqual(["identificador", "bl", "estado", "pendientes", "shipper", "eta"]);
  });
});
