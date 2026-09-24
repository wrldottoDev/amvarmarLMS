import { describe, expect, it } from "vitest";
import {
  filtrosDesdeParametros,
  parametrosDeFiltros,
  type FiltrosCarga,
} from "./filtros-cargas";

describe("filtros de cargas", () => {
  it("conserva filtros combinados al pasar por la URL", () => {
    const filtros: FiltrosCarga = {
      q: "  proveedor alfa  ",
      shipmentNumber: "SHP-000042",
      wr: "WR-9081",
      shipper: "ACME",
      carrier: "Carrier Uno",
      reference: "PO-123",
      referenceType: "PO",
      companyId: "6ca45b33-f404-4bba-9e95-9438395ed495",
      estados: ["IN_TRANSIT", "STORED"],
      etaDesde: "2026-08-01",
      etaHasta: "2026-08-31",
    };

    const parametros = parametrosDeFiltros(filtros);

    expect(filtrosDesdeParametros(parametros)).toEqual({
      ...filtros,
      q: "proveedor alfa",
    });
    expect(parametros.getAll("status")).toEqual(["IN_TRANSIT", "STORED"]);
  });

  it("descarta estados y tipos de referencia desconocidos", () => {
    const parametros = new URLSearchParams(
      "status=STORED&status=HOLD&reference_type=PASSWORD&q=contenedor",
    );

    expect(filtrosDesdeParametros(parametros)).toMatchObject({
      q: "contenedor",
      estados: ["STORED"],
      referenceType: "",
    });
  });

  it("omite campos vacíos de la URL", () => {
    const parametros = parametrosDeFiltros({
      q: "   ",
      shipmentNumber: "",
      wr: "",
      shipper: "",
      carrier: "",
      reference: "",
      referenceType: "",
      companyId: "",
      estados: [],
      etaDesde: "",
      etaHasta: "",
    });

    expect(parametros.toString()).toBe("");
  });
});
