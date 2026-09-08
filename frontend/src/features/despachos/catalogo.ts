import type { paths } from "@/lib/api/generated";

/**
 * Estados que el backend acepta como filtro.
 *
 * Se deriva del esquema generado en vez de escribirse a mano: si el backend
 * agrega o quita un estado, esto deja de compilar en vez de fallar en tiempo de
 * ejecución con un filtro que el servidor rechaza.
 */
export type EstadoDespacho = NonNullable<
  NonNullable<
    paths["/api/v1/dispatch-requests"]["get"]["parameters"]["query"]
  >["status"]
> extends readonly (infer U)[]
  ? U
  : never;

/**
 * Cómo se le llama a cada estado en pantalla.
 *
 * El backend usa nombres técnicos en inglés porque son un contrato estable. Un
 * cliente que ve `DISPATCH_REQUESTED` tiene que aprender qué significa, y no
 * hay razón para pedirle eso: la traducción vive acá y la pantalla nunca
 * muestra el código.
 */
export const etiquetaDespacho: Record<string, string> = {
  PENDING: "Esperando aprobación",
  APPROVED: "Aprobado",
  PREPARING: "Preparando",
  DISPATCHED: "Despachado",
  COMPLETED: "Completado",
  REJECTED: "Rechazado",
  CANCELLED: "Cancelado",
};

/** Qué significa cada estado, en una frase, para quien nunca vio el sistema. */
export const explicacionDespacho: Record<string, string> = {
  PENDING: "Recibimos tu solicitud. Operaciones la va a revisar.",
  APPROVED: "Aprobado. Estamos coordinando el envío.",
  PREPARING: "Estamos preparando tu carga para salir.",
  DISPATCHED: "La carga ya salió físicamente de bodega.",
  COMPLETED: "El despacho quedó cerrado con toda su documentación.",
  REJECTED: "No se pudo procesar. Abajo está el motivo.",
  CANCELLED: "La solicitud se canceló.",
};

export const tonoDespacho: Record<string, "espera" | "avance" | "listo" | "alto"> = {
  PENDING: "espera",
  APPROVED: "avance",
  PREPARING: "avance",
  DISPATCHED: "avance",
  COMPLETED: "listo",
  REJECTED: "alto",
  CANCELLED: "alto",
};

/**
 * Estados desde los que un cliente todavía puede cancelar (ADR-0013).
 *
 * Después de la aprobación puede haber contenedor reservado o transporte
 * contratado. La interfaz oculta el botón en vez de mostrarlo y que el servidor
 * lo rechace: ofrecer algo que va a fallar es peor que no ofrecerlo.
 */
export const clienteCancelaDesde: readonly string[] = ["PENDING"];

export const metodos = [
  { valor: "SEA", etiqueta: "Marítimo", ayuda: "Más económico. Tarda más." },
  { valor: "AIR", etiqueta: "Aéreo", ayuda: "Más rápido. Cuesta más." },
  { valor: "LAND", etiqueta: "Terrestre", ayuda: "Para envíos dentro de la región." },
] as const;
