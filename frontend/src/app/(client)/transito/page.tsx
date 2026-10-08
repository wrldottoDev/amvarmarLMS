"use client";

import { Info } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import PaginaCargas from "@/app/(client)/shipments/page";
import { PestanasVista } from "@/components/shipments/pestanas-vista";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import type { EstadoCarga } from "@/lib/api/tipos";

/**
 * Lo que viaja y lo que ya llegó, separados como en Miami (pedido de AMVARMAR,
 * 2026-10-08). «En Costa Rica» es el inventario de los tránsitos completados:
 * todos, también los archivados. Historial los sigue mostrando junto a Miami.
 */
const PESTANAS: { clave: string; titulo: string; ayuda: string; estados: EstadoCarga[] }[] = [
  {
    clave: "camino",
    titulo: "En camino",
    ayuda: "Reportes de tránsito que todavía viajan a destino.",
    estados: ["PRE_ALERT", "BOOKING_ASSIGNED", "IN_TRANSIT", "TRANSSHIPMENT"],
  },
  {
    clave: "destino",
    titulo: "En Costa Rica (completados)",
    ayuda: "Reportes de tránsito que ya llegaron a destino o se entregaron.",
    estados: ["AT_DESTINATION", "DELIVERED"],
  },
];

export default function PaginaTransito() {
  return (
    <Suspense fallback={<CargandoPagina texto="Preparando reportes de tránsito" />}>
      <ContenidoTransito />
    </Suspense>
  );
}

function ContenidoTransito() {
  const vista = useSearchParams().get("vista");
  const actual = PESTANAS.find((p) => p.clave === vista) ?? PESTANAS[0];
  const enCamino = actual.clave === "camino";

  return (
    <PaginaCargas
      key={actual.clave}
      tipoOrigen="TRANSIT"
      estadosVista={actual.estados}
      incluirArchivadas={!enCamino}
      encabezado={
        <div className="space-y-4">
          <PestanasVista
            ruta="/transito"
            etiqueta="Secciones de reportes de tránsito"
            pestanas={PESTANAS}
            actual={actual.clave}
            ayuda={actual.ayuda}
          />
          {enCamino ? (
            <p className="flex items-start gap-2 rounded-md border border-[var(--advertencia-borde)] bg-[var(--advertencia-tenue)] px-4 py-3 text-sm text-[var(--advertencia)]">
              <Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              Los tiempos estimados de arribo (ETA) pueden variar y están sujetos a cambios.
            </p>
          ) : null}
        </div>
      }
    />
  );
}
