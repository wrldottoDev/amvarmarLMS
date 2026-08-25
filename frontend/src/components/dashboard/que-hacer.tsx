"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CheckCircle2, FileWarning, Truck } from "lucide-react";
import Link from "next/link";
import { api, exigirDatos } from "@/lib/api/client";

/**
 * Lo primero que ve el cliente: qué tiene que hacer hoy.
 *
 * El tablero de tarjetas responde "cómo van mis cargas". Esto responde "¿tengo
 * que hacer algo?", que es la pregunta con la que la gente entra. Sin este
 * bloque hay que deducirlo cruzando contadores, y eso es trabajo que el sistema
 * puede hacer solo.
 *
 * Si no hay nada pendiente, lo dice. Un panel vacío deja la duda de si cargó mal.
 */
export function QueHacer() {
  const documentos = useQuery({
    queryKey: ["cargas", "requieren-accion-cliente"],
    queryFn: async () =>
      exigirDatos(
        await api.GET("/api/v1/shipments", {
          params: { query: { limit: 100, archived: false } },
        }),
      ),
  });

  if (documentos.isPending || documentos.error) return null;

  const cargas = documentos.data.items;
  const conDocumentosPendientes = cargas.filter((c) => c.client_action_required_count > 0);
  const listasParaDespachar = cargas.filter(
    (c) => c.status === "STORED" && c.client_action_required_count === 0,
  );

  const pendientes = [
    conDocumentosPendientes.length > 0
      ? {
          clave: "documentos",
          icono: FileWarning,
          estilo: "border-[#f2d9a0] bg-[#fff6e5] text-[#8a5b00]",
          titulo:
            conDocumentosPendientes.length === 1
              ? "Falta un documento en una carga"
              : `Faltan documentos en ${conDocumentosPendientes.length} cargas`,
          detalle: "Sin ellos no podemos despachar.",
          enlace: "/shipments?accion=documentos",
          accion: "Ver cuáles",
        }
      : null,
    listasParaDespachar.length > 0
      ? {
          clave: "despacho",
          icono: Truck,
          estilo: "border-[#bcd6dd] bg-[#e8f0f2] text-[var(--mar)]",
          titulo:
            listasParaDespachar.length === 1
              ? "Tenés 1 carga lista para despachar"
              : `Tenés ${listasParaDespachar.length} cargas listas para despachar`,
          detalle: "Están en bodega esperando tu solicitud.",
          enlace: "/despachos/nuevo",
          accion: "Solicitar despacho",
        }
      : null,
  ].filter((item) => item !== null);

  if (pendientes.length === 0) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-[#b7e0c2] bg-[#e9f6ec] px-4 py-3.5 text-sm text-[#1c6b33]">
        <CheckCircle2 className="size-5 shrink-0" aria-hidden="true" />
        <p>
          <strong>Todo al día.</strong> No hay nada pendiente de tu lado.
        </p>
      </div>
    );
  }

  return (
    <section className="space-y-2" aria-label="Pendientes">
      {pendientes.map((item) => {
        const Icono = item.icono;
        return (
          <Link
            key={item.clave}
            href={item.enlace}
            className={`flex items-center gap-3 rounded-lg border px-4 py-3.5 hover:brightness-[0.98] ${item.estilo}`}
          >
            <Icono className="size-5 shrink-0" aria-hidden="true" />
            <span className="min-w-0 flex-1">
              <strong className="block text-sm">{item.titulo}</strong>
              <span className="block text-sm opacity-80">{item.detalle}</span>
            </span>
            <span className="flex shrink-0 items-center gap-1 text-sm font-semibold">
              {item.accion}
              <ArrowRight className="size-4" aria-hidden="true" />
            </span>
          </Link>
        );
      })}
    </section>
  );
}
