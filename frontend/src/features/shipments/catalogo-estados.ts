import type { EstadoCarga } from "@/lib/api/tipos";

export const estadosCarga: readonly EstadoCarga[] = [
  "PRE_ALERT",
  "IN_TRANSIT",
  "RECEIVED",
  "STORED",
  "DISPATCH_REQUESTED",
  "PREPARING",
  "DISPATCHED",
  "DELIVERED",
  "CANCELLED",
];

export const etiquetaEstado: Record<EstadoCarga, string> = {
  PRE_ALERT: "Prealerta",
  IN_TRANSIT: "En tránsito",
  RECEIVED: "Recibida",
  STORED: "Almacenada",
  DISPATCH_REQUESTED: "Despacho solicitado",
  PREPARING: "En preparación",
  DISPATCHED: "Despachada",
  DELIVERED: "Entregada",
  CANCELLED: "Cancelada",
};

export function esEstadoCarga(valor: string): valor is EstadoCarga {
  return estadosCarga.some((estado) => estado === valor);
}
