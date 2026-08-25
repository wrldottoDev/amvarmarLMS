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
