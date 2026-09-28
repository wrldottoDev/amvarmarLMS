import type { CargaResumen } from "@/lib/api/tipos";

export function alternarCarga(
  seleccionadas: ReadonlyMap<string, CargaResumen>,
  carga: CargaResumen,
) {
  const siguiente = new Map(seleccionadas);
  if (siguiente.has(carga.id)) siguiente.delete(carga.id);
  else siguiente.set(carga.id, carga);
  return siguiente;
}

export function alternarCargasVisibles(
  seleccionadas: ReadonlyMap<string, CargaResumen>,
  visibles: CargaResumen[],
) {
  const siguiente = new Map(seleccionadas);
  const todasVisibles = visibles.length > 0 && visibles.every((carga) => siguiente.has(carga.id));
  for (const carga of visibles) {
    if (todasVisibles) siguiente.delete(carga.id);
    else siguiente.set(carga.id, carga);
  }
  return siguiente;
}
