import { describe, expect, it } from "vitest";
import { mensajeVerificacion } from "./verificacion";

describe("mensajeVerificacion", () => {
  it("confirma el envío con el correo de destino", () => {
    expect(mensajeVerificacion("ENVIADO", "ana@ejemplo.com")).toEqual({
      texto: "Enviamos el enlace de verificación a ana@ejemplo.com.",
      exito: true,
    });
  });

  it("no presenta como error un correo que ya estaba verificado", () => {
    expect(mensajeVerificacion("YA_VERIFICADO", "ana@ejemplo.com").exito).toBe(true);
  });

  it("avisa cuando el correo no salió", () => {
    const mensaje = mensajeVerificacion("NO_ENVIADO", "ana@ejemplo.com");
    expect(mensaje.exito).toBe(false);
    expect(mensaje.texto).toContain("No se pudo enviar");
  });
});
