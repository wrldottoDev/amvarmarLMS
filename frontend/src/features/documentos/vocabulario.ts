/**
 * Estado del requisito, dicho para quien tiene que actuar.
 *
 * El backend distingue seis estados porque la auditoría los necesita. Al
 * cliente solo le importa una cosa: ¿tengo que hacer algo o no?
 */
export const estadoRequisito: Record<
  string,
  { etiqueta: string; explicacion: string; tono: "falta" | "espera" | "listo" | "alto" }
> = {
  PENDING: {
    etiqueta: "Falta subirlo",
    explicacion: "Todavía no lo recibimos.",
    tono: "falta",
  },
  UPLOADED: {
    etiqueta: "En revisión",
    explicacion: "Lo recibimos y Operaciones lo está revisando.",
    tono: "espera",
  },
  VERIFIED: {
    etiqueta: "Aprobado",
    explicacion: "Revisado y aceptado.",
    tono: "listo",
  },
  REJECTED: {
    etiqueta: "Rechazado",
    explicacion: "Hay que volver a subirlo.",
    tono: "alto",
  },
  WAIVED: {
    etiqueta: "No hace falta",
    explicacion: "Operaciones lo exoneró para esta carga.",
    tono: "listo",
  },
  OPEN: { etiqueta: "Pendiente", explicacion: "Queda algo por resolver.", tono: "falta" },
  FULFILLED: { etiqueta: "Resuelto", explicacion: "Ya está.", tono: "listo" },
};

/**
 * Por qué un documento todavía no se puede abrir.
 *
 * Solo queda el estado de la subida: el antivirus se retiró por decisión de
 * AMVARMAR y un documento es descargable en cuanto terminó de subirse.
 */
export const estadoSubida: Record<string, string> = {
  UPLOADING: "Subiendo el archivo…",
  READY: "",
  FAILED: "La subida no se completó. Volvé a cargar el archivo.",
};

export function sePuedeDescargar(subida: string) {
  return subida === "READY";
}
