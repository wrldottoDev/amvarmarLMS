import { clsx, type ClassValue } from "clsx";

export function clases(...valores: ClassValue[]) {
  return clsx(valores);
}

const formatoFecha = new Intl.DateTimeFormat("es-CR", {
  day: "2-digit",
  month: "short",
  year: "numeric",
});

const formatoFechaHora = new Intl.DateTimeFormat("es-CR", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function formatearFecha(valor: string | null | undefined) {
  if (!valor) return "Sin fecha";
  const fecha = new Date(valor);
  return Number.isNaN(fecha.getTime()) ? "Sin fecha" : formatoFecha.format(fecha);
}

export function formatearFechaHora(valor: string | null | undefined) {
  if (!valor) return "Sin registro";
  const fecha = new Date(valor);
  return Number.isNaN(fecha.getTime()) ? "Sin registro" : formatoFechaHora.format(fecha);
}

export function iniciales(nombre: string, apellido: string) {
  return `${nombre.trim().charAt(0)}${apellido.trim().charAt(0)}`.toUpperCase() || "AM";
}

const formatoRelativo = new Intl.RelativeTimeFormat("es", { numeric: "auto" });

const TRAMOS: readonly [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 60 * 60],
  ["month", 30 * 24 * 60 * 60],
  ["day", 24 * 60 * 60],
  ["hour", 60 * 60],
  ["minute", 60],
];

/**
 * "hace 3 horas" en vez de una fecha completa.
 *
 * Para un aviso importa cuán reciente es, no el día exacto. Una fecha obliga a
 * hacer la resta mentalmente cada vez.
 */
export function tiempoRelativo(valor: string | null | undefined) {
  if (!valor) return "";
  const fecha = new Date(valor);
  if (Number.isNaN(fecha.getTime())) return "";

  const segundos = Math.round((fecha.getTime() - Date.now()) / 1000);
  const absoluto = Math.abs(segundos);

  for (const [unidad, tamano] of TRAMOS) {
    if (absoluto >= tamano) {
      return formatoRelativo.format(Math.round(segundos / tamano), unidad);
    }
  }
  return "hace un momento";
}
