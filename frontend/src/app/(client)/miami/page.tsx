"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import PaginaCargas from "@/app/(client)/shipments/page";
import { PestanasVista } from "@/components/shipments/pestanas-vista";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import type { EstadoCarga } from "@/lib/api/tipos";

/**
 * Miami en tres pestañas, en el orden en que la carga avanza.
 * Lo que viene en camino no se muestra: aparece al llegar a la bodega, como
 * en el sistema viejo (pedido de AMVARMAR, 2026-10-08). El sistema viejo
 * separaba "todo lo activo" de "lo que puedo despachar"; mezclarlo en una sola
 * lista era lo que los clientes no entendían.
 */
const PESTANAS: { clave: string; titulo: string; ayuda: string; estados: EstadoCarga[] }[] = [
  {
    clave: "inventario",
    titulo: "Inventario en Miami",
    ayuda: "Lo que está en la bodega. Marcá las almacenadas y tocá «Solicitar despacho».",
    estados: ["RECEIVED", "STORED"],
  },
  {
    clave: "despacho",
    titulo: "En despacho",
    ayuda: "Cargas con un despacho pedido o en preparación. Lo despachado pasa a «Despachos completados».",
    estados: ["DISPATCH_REQUESTED", "PREPARING"],
  },
  {
    // Todas, también las archivadas: Historial las sigue mostrando junto a tránsito.
    clave: "completados",
    titulo: "Despachos completados",
    ayuda: "Cargas de Miami ya despachadas o entregadas.",
    estados: ["DISPATCHED", "AT_DESTINATION", "DELIVERED"],
  },
];

export default function PaginaMiami() {
  return (
    <Suspense fallback={<CargandoPagina texto="Preparando Miami" />}>
      <ContenidoMiami />
    </Suspense>
  );
}

function ContenidoMiami() {
  const vista = useSearchParams().get("vista");
  const actual = PESTANAS.find((p) => p.clave === vista) ?? PESTANAS[0];

  return (
    <PaginaCargas
      // Otra pestaña es otra lista: sin la clave quedaría la selección de la anterior.
      key={actual.clave}
      tipoOrigen="MIAMI"
      estadosVista={actual.estados}
      seleccionParaDespacho={actual.clave === "inventario"}
      incluirArchivadas={actual.clave === "completados"}
      encabezado={
        <PestanasVista
          ruta="/miami"
          etiqueta="Secciones de Miami"
          pestanas={PESTANAS}
          actual={actual.clave}
          ayuda={actual.ayuda}
        />
      }
    />
  );
}
