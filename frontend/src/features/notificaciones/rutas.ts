import type { Notificacion } from "@/lib/api/tipos";

/**
 * A dónde lleva un aviso al tocarlo.
 *
 * Un aviso que no lleva a ningún lado obliga a buscar la carga a mano, que es
 * justo el trabajo que el aviso venía a ahorrar.
 */
export function rutaDeRecurso(aviso: Notificacion): string {
  if (!aviso.resource_id) return "/avisos";

  switch (aviso.resource_type) {
    case "shipment":
      return `/shipments/${aviso.resource_id}`;
    case "dispatch_request":
      return `/despachos/${aviso.resource_id}`;
    default:
      return "/avisos";
  }
}
