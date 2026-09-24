import { describe, expect, it } from "vitest";
import { revisionDeRequisito } from "./revision";

const subido = { status: "UPLOADED", document_id: "doc-1" };

describe("revisionDeRequisito", () => {
  it("ofrece revisar un requisito subido con su archivo listo", () => {
    expect(revisionDeRequisito(subido, "READY", true)).toEqual({ mostrar: true, listo: true });
  });

  it("no ofrece nada a quien no puede verificar documentos", () => {
    expect(revisionDeRequisito(subido, "READY", false).mostrar).toBe(false);
  });

  it("solo un requisito subido se revisa", () => {
    for (const status of ["PENDING", "VERIFIED", "REJECTED", "WAIVED", "OPEN"]) {
      expect(revisionDeRequisito({ ...subido, status }, "READY", true).mostrar).toBe(false);
    }
  });

  it("sin documento no hay nada que revisar", () => {
    expect(revisionDeRequisito({ status: "UPLOADED", document_id: null }, undefined, true).mostrar).toBe(
      false,
    );
  });

  it("muestra la revisión pero la deja en espera mientras el archivo se procesa", () => {
    expect(revisionDeRequisito(subido, "PROCESSING", true)).toEqual({
      mostrar: true,
      listo: false,
      aviso: "El archivo todavía se está procesando.",
    });
  });
});
