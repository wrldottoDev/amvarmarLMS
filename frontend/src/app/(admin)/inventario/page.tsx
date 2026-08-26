"use client";

import PaginaCargas from "@/app/(client)/shipments/page";

/**
 * La vista Inventario del sistema viejo.
 *
 * Es el mismo listado sin filtrar por estado: sirve para buscar una carga
 * cuando no se sabe en qué punto está, que es la mitad de las veces que alguien
 * busca algo.
 */
export default function PaginaInventario() {
  return <PaginaCargas inventario />;
}
