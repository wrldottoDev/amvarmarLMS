import type { EstadoCarga } from "@/lib/api/tipos";

export const estadosCarga: readonly EstadoCarga[] = [
  "PRE_ALERT",
  "BOOKING_ASSIGNED",
  "IN_TRANSIT",
  "TRANSSHIPMENT",
  "RECEIVED",
  "STORED",
  "DISPATCH_REQUESTED",
  "PREPARING",
  "DISPATCHED",
  "AT_DESTINATION",
  "DELIVERED",
  "CANCELLED",
];

export const etiquetaEstado: Record<EstadoCarga, string> = {
  PRE_ALERT: "Prealerta",
  BOOKING_ASSIGNED: "Booking asignado",
  IN_TRANSIT: "En tránsito",
  TRANSSHIPMENT: "Transbordo",
  RECEIVED: "Recibida",
  STORED: "Almacenada",
  DISPATCH_REQUESTED: "Despacho solicitado",
  PREPARING: "En preparación",
  DISPATCHED: "Despachada",
  AT_DESTINATION: "En destino",
  DELIVERED: "Entregada",
  CANCELLED: "Cancelada",
};

export function esEstadoCarga(valor: string): valor is EstadoCarga {
  return estadosCarga.some((estado) => estado === valor);
}
