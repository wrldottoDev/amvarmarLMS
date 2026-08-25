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
 * El antivirus corre después de subir (Paso 3.2), así que un documento recién
 * subido no se puede descargar todavía. Decirlo evita que parezca un error.
 */
export const estadoEscaneo: Record<string, string> = {
  PENDING: "Revisando el archivo…",
  CLEAN: "",
  INFECTED: "El archivo tiene un problema de seguridad y no se puede abrir.",
  FAILED: "No se pudo revisar el archivo.",
};

export function sePuedeDescargar(escaneo: string, subida: string) {
  return escaneo === "CLEAN" && subida === "READY";
}
