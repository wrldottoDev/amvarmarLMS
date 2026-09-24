"use client";

import { ArrowRight, Check, FolderOpen } from "lucide-react";
import Link from "next/link";
import { use } from "react";
import { Expediente } from "@/components/documentos/expediente";
import { AvisoError } from "@/components/ui/aviso-error";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import { useSesion } from "@/features/auth/contexto-sesion";
import { useCarga } from "@/features/shipments/consultas";

/**
 * Segundo paso del alta: los papeles de la carga.
 *
 * Reproduce `warehouse_files` del sistema viejo, adonde `create_warehouse`
 * redirigía después de guardar. El motivo sigue siendo el mismo: quien acaba de
 * dar de alta una carga casi siempre tiene los documentos en la mano, y
 * obligarlo a buscarla de nuevo en el listado para adjuntarlos hace que la
 * mitad de las cargas terminen sin papeles.
 *
 * No es un paso obligatorio. "Terminar" lleva al detalle y los documentos se
 * pueden subir después desde ahí.
 */
export default function PaginaArchivosDeCarga({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const carga = useCarga(id);
  const { usuario } = useSesion();

  if (carga.isPending) return <CargandoPagina />;
  if (carga.isError) return <AvisoError error={carga.error} />;

  const datos = carga.data;

  return (
    <section className="mx-auto max-w-4xl space-y-4">
      <div className="rounded-lg border border-[var(--exito-borde)] bg-[var(--exito-tenue)] px-4 py-3">
        <p className="flex items-center gap-2 text-sm font-semibold text-[var(--exito)]">
          <Check className="size-4" aria-hidden="true" />
          Carga creada
        </p>
        <p className="mt-1 text-sm text-[var(--exito)]">
          {datos.wr || datos.invoice || datos.shipment_number} ·{" "}
          <span className="font-mono">{datos.shipment_number}</span>
        </p>
      </div>

      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-[var(--mar)]">
          <FolderOpen className="size-6" aria-hidden="true" />
          Documentos de la carga
        </h1>
        <p className="mt-1 text-sm text-[var(--texto-secundario)]">
          Adjuntá lo que tengas ahora. Se pueden agregar después desde el detalle.
        </p>
      </div>

      <Expediente cargaId={id} esCliente={Boolean(usuario?.empresa)} />

      <div className="flex justify-end gap-2 pb-4">
        <Link
          href={`/shipments/${id}`}
          className="flex h-10 items-center gap-1.5 rounded-md bg-[var(--mar)] px-4 text-sm font-semibold text-white hover:opacity-90"
        >
          Terminar
          <ArrowRight className="size-4" aria-hidden="true" />
        </Link>
      </div>
    </section>
  );
}
