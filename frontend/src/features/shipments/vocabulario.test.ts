import { describe, expect, it } from "vitest";
import { ubicacionParaCliente } from "./vocabulario";

const costaRica = { country_code: "CR", name: "San José" };

describe("ubicación para el cliente", () => {
  it("una carga de Miami dice si viene, si está en Miami o si ya salió", () => {
    const miami = (status: string) =>
      ubicacionParaCliente({ status, origin_kind: "MIAMI", destination: costaRica });
    expect(miami("PRE_ALERT")).toBe("En camino a Miami");
    expect(miami("STORED")).toBe("En Miami");
    expect(miami("PREPARING")).toBe("En Miami · preparando despacho");
    expect(miami("DISPATCHED")).toBe("En camino a Costa Rica");
    expect(miami("DELIVERED")).toBe("En Costa Rica");
  });

  it("un reporte de tránsito va de origen a destino", () => {
    const transito = (status: string) =>
      ubicacionParaCliente({ status, origin_kind: "TRANSIT", destination: costaRica });
    expect(transito("BOOKING_ASSIGNED")).toBe("En origen");
    expect(transito("TRANSSHIPMENT")).toBe("En tránsito a Costa Rica");
    expect(transito("AT_DESTINATION")).toBe("En Costa Rica");
  });

  it("fuera de Costa Rica usa el nombre del destino", () => {
    expect(
      ubicacionParaCliente({
        status: "AT_DESTINATION",
        origin_kind: "TRANSIT",
        destination: { country_code: "PA", name: "Colón" },
      }),
    ).toBe("En Colón");
  });
});
