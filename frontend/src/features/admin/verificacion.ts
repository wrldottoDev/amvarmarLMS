import type { components } from "@/lib/api/generated";

export type EstadoVerificacion = components["schemas"]["VerificacionCorreoResponse"]["estado"];

/** Qué decirle a quien administra después de pedir el reenvío. */
export function mensajeVerificacion(estado: EstadoVerificacion, correo: string): {
  texto: string;
  exito: boolean;
} {
  switch (estado) {
    case "ENVIADO":
      return { texto: `Enviamos el enlace de verificación a ${correo}.`, exito: true };
    case "YA_VERIFICADO":
      return { texto: `${correo} ya estaba verificado.`, exito: true };
    case "NO_ENVIADO":
      return {
        texto: `No se pudo enviar el correo a ${correo}. Intente de nuevo en unos minutos.`,
        exito: false,
      };
  }
}
