"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import PaginaCargas from "@/app/(client)/shipments/page";
import { PestanasVista } from "@/components/shipments/pestanas-vista";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import type { EstadoCarga } from "@/lib/api/tipos";

/**
 * Miami en tres pestañas, en el orden en que la carga avanza. El sistema viejo
 * separaba "todo lo activo" de "lo que puedo despachar"; mezclarlo en una sola
 * lista era lo que los clientes no entendían.
 */
const PESTANAS: { clave: string; titulo: string; ayuda: string; estados: EstadoCarga[] }[] = [
  {
    clave: "camino",
    titulo: "En camino a Miami",
    ayuda: "Cargas avisadas que todavía no llegan a la bodega de Miami.",
    estados: ["PRE_ALERT", "BOOKING_ASSIGNED", "IN_TRANSIT", "TRANSSHIPMENT"],
  },
  {
    clave: "inventario",
    titulo: "Inventario en Miami",
    ayuda: "Lo que está en la bodega. Marcá las almacenadas y tocá «Solicitar despacho».",
    estados: ["RECEIVED", "STORED"],
  },
  {
    clave: "despacho",
    titulo: "En despacho",
    ayuda: "Cargas con un despacho pedido o en preparación. Lo despachado pasa al Historial.",
    estados: ["DISPATCH_REQUESTED", "PREPARING"],
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
  const actual = PESTANAS.find((p) => p.clave === vista) ?? PESTANAS[1];

  return (
    <PaginaCargas
      // Otra pestaña es otra lista: sin la clave quedaría la selección de la anterior.
      key={actual.clave}
      tipoOrigen="MIAMI"
      estadosVista={actual.estados}
      seleccionParaDespacho={actual.clave === "inventario"}
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
