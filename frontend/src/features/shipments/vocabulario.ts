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
  BOOKING_ASSIGNED: "Ya tiene espacio reservado con la naviera.",
  IN_TRANSIT: "Va en camino.",
  TRANSSHIPMENT: "Está cambiando de buque rumbo a su destino.",
  RECEIVED: "Llegó a bodega y la estamos revisando.",
  STORED: "Está guardada y lista para que pidas el despacho.",
  DISPATCH_REQUESTED: "Pediste el despacho. Operaciones lo está revisando.",
  PREPARING: "La estamos preparando para salir.",
  DISPATCHED: "Salió de bodega.",
  AT_DESTINATION: "Llegó a destino.",
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

const EN_CAMINO = new Set<EstadoCarga>(["PRE_ALERT", "BOOKING_ASSIGNED", "IN_TRANSIT", "TRANSSHIPMENT"]);

/**
 * Dónde está la carga, en las palabras del cliente (pedido de AMVARMAR,
 * 2026-10-08): "En Miami", "En Costa Rica", en vez de "Prealerta" o
 * "Recibida". El estado exacto sigue visible como detalle.
 */
export function ubicacionParaCliente(carga: {
  status: string;
  origin_kind: "MIAMI" | "TRANSIT";
  destination: { country_code: string; name: string };
}): string {
  const destino = carga.destination.country_code === "CR" ? "Costa Rica" : carga.destination.name;
  const estado = carga.status as EstadoCarga;
  if (estado === "CANCELLED") return "Cancelada";
  if (estado === "AT_DESTINATION" || estado === "DELIVERED") return `En ${destino}`;
  if (carga.origin_kind === "MIAMI") {
    if (EN_CAMINO.has(estado)) return "En camino a Miami";
    if (estado === "RECEIVED" || estado === "STORED") return "En Miami";
    if (estado === "DISPATCH_REQUESTED" || estado === "PREPARING") return "En Miami · preparando despacho";
    return `En camino a ${destino}`;
  }
  if (estado === "PRE_ALERT" || estado === "BOOKING_ASSIGNED") return "En origen";
  return `En tránsito a ${destino}`;
}
