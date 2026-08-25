"use client";

import { ArrowLeft, PackagePlus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import {
  useBodegas,
  useCrearCarga,
  useEmpresas,
  useUbicaciones,
} from "@/features/admin/consultas";
import { clases } from "@/lib/utilidades";

export default function PaginaNuevaCarga() {
  const router = useRouter();
  const empresas = useEmpresas();
  const ubicaciones = useUbicaciones();
  const bodegas = useBodegas();
  const crear = useCrearCarga();

  const [empresa, setEmpresa] = useState("");
  const [origen, setOrigen] = useState("");
  const [destino, setDestino] = useState("");
  const [bodega, setBodega] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [direccion, setDireccion] = useState("");
  const [eta, setEta] = useState("");
  const [peso, setPeso] = useState("");
  const [permiso, setPermiso] = useState(false);

  if (empresas.isPending || ubicaciones.isPending) return <CargandoPagina />;

  const bodegasDelOrigen = (bodegas.data ?? []).filter((b) => b.location_id === origen);
  const listo = Boolean(empresa && origen && destino && origen !== destino);

  async function guardar() {
    const creada = await crear.mutateAsync({
      company_id: empresa,
      origin_location_id: origen,
      destination_location_id: destino,
      origin_facility_id: bodega || null,
      destination_address: direccion.trim() || null,
      description: descripcion.trim() || null,
      estimated_arrival_at: eta ? new Date(`${eta}T12:00:00`).toISOString() : null,
      weight_kg: peso ? peso : null,
      permit_review_required: permiso,
    });
    router.push(`/shipments/${creada.id}`);
  }

  return (
    <section className="mx-auto max-w-3xl space-y-5">
      <Link
        href="/shipments"
        className="inline-flex items-center gap-1.5 text-sm text-[var(--texto-secundario)] hover:underline"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        Volver a cargas
      </Link>

      <header>
        <h1 className="text-xl font-bold">Nueva carga</h1>
        <p className="mt-1 text-sm text-[var(--texto-secundario)]">
          La carga nace como prealerta. Cuando llegue a bodega la vas moviendo de estado desde su
          detalle.
        </p>
      </header>

      <div className="space-y-4 rounded-md border bg-white px-4 py-4">
        <label className="block">
          <span className="mb-1 block text-sm font-medium">¿De qué cliente es?</span>
          <select
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={empresa}
            onChange={(evento) => setEmpresa(evento.target.value)}
          >
            <option value="">Elegí una empresa…</option>
            {empresas.data?.map((e) => (
              <option key={e.id} value={e.id}>
                {e.trade_name || e.legal_name}
              </option>
            ))}
          </select>
        </label>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="mb-1 block text-sm font-medium">Sale de</span>
            <select
              className="w-full rounded-md border px-3 py-2 text-sm"
              value={origen}
              onChange={(evento) => {
                setOrigen(evento.target.value);
                setBodega("");
              }}
            >
              <option value="">Origen…</option>
              {ubicaciones.data?.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name} ({u.location_code})
                </option>
              ))}
            </select>
          </label>

          <label className="block">
            <span className="mb-1 block text-sm font-medium">Llega a</span>
            <select
              className={clases(
                "w-full rounded-md border px-3 py-2 text-sm",
                origen && destino && origen === destino && "border-[var(--peligro)]",
              )}
              value={destino}
              onChange={(evento) => setDestino(evento.target.value)}
            >
              <option value="">Destino…</option>
              {ubicaciones.data?.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name} ({u.location_code})
                </option>
              ))}
            </select>
            {origen && destino && origen === destino ? (
              <span className="mt-1 block text-xs text-[var(--peligro)]">
                El origen y el destino no pueden ser el mismo lugar.
              </span>
            ) : null}
          </label>
        </div>

        {bodegasDelOrigen.length > 0 ? (
          <label className="block">
            <span className="mb-1 block text-sm font-medium">
              Bodega de origen{" "}
              <span className="font-normal text-[var(--texto-secundario)]">(opcional)</span>
            </span>
            <select
              className="w-full rounded-md border px-3 py-2 text-sm"
              value={bodega}
              onChange={(evento) => setBodega(evento.target.value)}
            >
              <option value="">Sin bodega</option>
              {bodegasDelOrigen.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.facility_code}
                  {b.uses_warehouse_receipt ? " — emite Warehouse Receipt" : ""}
                </option>
              ))}
            </select>
            {bodegasDelOrigen.find((b) => b.id === bodega)?.uses_warehouse_receipt ? (
              <span className="mt-1 block text-xs text-[var(--texto-secundario)]">
                Esta bodega emite WR: la carga va a exigirlo antes de darse por almacenada.
              </span>
            ) : null}
          </label>
        ) : null}

        <label className="block">
          <span className="mb-1 block text-sm font-medium">
            ¿Qué viene? <span className="font-normal text-[var(--texto-secundario)]">(opcional)</span>
          </span>
          <textarea
            className="w-full rounded-md border px-3 py-2 text-sm"
            rows={2}
            value={descripcion}
            onChange={(evento) => setDescripcion(evento.target.value)}
            placeholder="Repuestos industriales, proveedor XYZ"
          />
        </label>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="mb-1 block text-sm font-medium">
              Llegada estimada{" "}
              <span className="font-normal text-[var(--texto-secundario)]">(opcional)</span>
            </span>
            <input
              type="date"
              className="w-full rounded-md border px-3 py-2 text-sm"
              value={eta}
              onChange={(evento) => setEta(evento.target.value)}
            />
          </label>

          <label className="block">
            <span className="mb-1 block text-sm font-medium">
              Peso en kg <span className="font-normal text-[var(--texto-secundario)]">(opcional)</span>
            </span>
            <input
              type="number"
              min="0"
              step="0.001"
              className="w-full rounded-md border px-3 py-2 text-sm"
              value={peso}
              onChange={(evento) => setPeso(evento.target.value)}
            />
          </label>
        </div>

        <label className="block">
          <span className="mb-1 block text-sm font-medium">
            Dirección de entrega{" "}
            <span className="font-normal text-[var(--texto-secundario)]">(opcional)</span>
          </span>
          <input
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={direccion}
            onChange={(evento) => setDireccion(evento.target.value)}
          />
        </label>

        <label className="flex items-start gap-2 rounded-md border border-[#f2d9a0] bg-[#fff6e5] px-3 py-2.5">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-[#8a5b00]"
            checked={permiso}
            onChange={(evento) => setPermiso(evento.target.checked)}
          />
          <span className="text-sm text-[#8a5b00]">
            <strong className="block">Puede requerir permiso o inspección</strong>
            Marcalo si la mercancía es regulada. Le vamos a pedir el permiso especial al cliente
            antes de despachar.
          </span>
        </label>
      </div>

      {crear.error ? <AvisoError error={crear.error} /> : null}

      <div className="flex justify-end gap-2">
        <Link
          href="/shipments"
          className="flex h-10 items-center rounded-md border px-4 text-sm font-medium hover:bg-[#edf1f2]"
        >
          Cancelar
        </Link>
        <Boton onClick={() => void guardar()} disabled={!listo} cargando={crear.isPending}>
          <PackagePlus className="size-4" aria-hidden="true" />
          Crear carga
        </Boton>
      </div>
    </section>
  );
}
