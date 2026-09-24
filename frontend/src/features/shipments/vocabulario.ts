import type { EstadoCarga } from "@/lib/api/tipos";

/**
 * Lo que el cliente necesita entender de cada estado, sin jerga.
 *
 * `etiquetaEstado` da el nombre corto para las insignias. Esto da la frase que
 * responde la pregunta real: ¿qué está pasando con mi carga y tengo que hacer
 * algo?
 */
export const queSignifica: Record<EstadoCarga, string> = {
  PRE_ALERT: "Nos avisaste que viene. Todavía no llega a bodega.",
  IN_TRANSIT: "Va en camino a nuestra bodega.",
  RECEIVED: "Llegó a bodega y la estamos revisando.",
  STORED: "Está guardada y lista para que pidas el despacho.",
  DISPATCH_REQUESTED: "Pediste el despacho. Operaciones lo está revisando.",
  PREPARING: "La estamos preparando para salir.",
  DISPATCHED: "Salió de bodega.",
  DELIVERED: "Entregada.",
  CANCELLED: "Cancelada.",
};

/**
 * Qué puede hacer el cliente ahora. Vacío significa que no hay nada que hacer,
 * y eso también es información: evita que alguien busque un botón inexistente.
 */
export const queHacerAhora: Partial<Record<EstadoCarga, string>> = {
  STORED: "Ya podés solicitar el despacho.",
  DISPATCHED: "Podés seguir el envío desde el detalle.",
};
