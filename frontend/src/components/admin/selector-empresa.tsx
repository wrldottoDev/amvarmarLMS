"use client";

import { Building2, Plus } from "lucide-react";
import { useState } from "react";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { useCrearEmpresa, useEmpresas } from "@/features/admin/consultas";

/**
 * Elegir el cliente de una carga, con alta en el mismo lugar.
 *
 * Quien alimenta el sistema recibe embarques de clientes que todavía no están
 * dados de alta. Obligarlo a irse a Empresas, crearla y volver significa perder
 * lo que ya escribió en el formulario de la carga, así que la salida real era
 * anotar la carga bajo otro cliente y arreglarlo después — que es justo lo que
 * produjo las cargas sin dueño de la base vieja.
 *
 * Solo pide el nombre legal, que es lo único obligatorio. El resto de la ficha
 * de la empresa se completa después desde Empresas; acá el objetivo es no
 * frenar la carga.
 */
export function SelectorEmpresa({
  valor,
  alCambiar,
}: {
  valor: string;
  alCambiar: (companyId: string) => void;
}) {
  const empresas = useEmpresas();
  const crear = useCrearEmpresa();

  const [creando, setCreando] = useState(false);
  const [nombre, setNombre] = useState("");
  const [cedula, setCedula] = useState("");

  async function guardar() {
    const nueva = await crear.mutateAsync({
      legal_name: nombre.trim(),
      tax_id: cedula.trim() || null,
    });
    // Queda seleccionada: si no, habría que buscarla en una lista que puede
    // tener cientos de nombres, justo después de haberla escrito.
    alCambiar(nueva.id);
    setCreando(false);
    setNombre("");
    setCedula("");
  }

  if (creando) {
    return (
      <div className="space-y-3 rounded-md border border-[var(--mar)] bg-[#f2f7f8] px-3 py-3">
        <p className="flex items-center gap-2 text-sm font-semibold">
          <Building2 className="size-4" aria-hidden="true" />
          Cliente nuevo
        </p>

        <label className="block">
          <span className="mb-1 block text-sm font-medium">Nombre o razón social</span>
          <input
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={nombre}
            autoFocus
            onChange={(evento) => setNombre(evento.target.value)}
            placeholder="Importadora del Istmo, S.A."
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-medium">
            RUC o cédula{" "}
            <span className="font-normal text-[var(--texto-secundario)]">(opcional)</span>
          </span>
          <input
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={cedula}
            onChange={(evento) => setCedula(evento.target.value)}
          />
        </label>

        {crear.error ? <AvisoError error={crear.error} /> : null}

        <div className="flex gap-2">
          <Boton
            onClick={() => void guardar()}
            disabled={nombre.trim().length === 0}
            cargando={crear.isPending}
          >
            Crear y usar
          </Boton>
          <button
            type="button"
            className="flex h-10 items-center rounded-md border bg-white px-4 text-sm font-medium hover:bg-[#edf1f2]"
            onClick={() => {
              setCreando(false);
              crear.reset();
            }}
          >
            Cancelar
          </button>
        </div>

        <p className="text-xs text-[var(--texto-secundario)]">
          El resto de la ficha se completa después desde Empresas.
        </p>
      </div>
    );
  }

  return (
    <div className="flex gap-2">
      <select
        className="w-full rounded-md border px-3 py-2 text-sm"
        value={valor}
        onChange={(evento) => alCambiar(evento.target.value)}
      >
        <option value="">Elegí una empresa…</option>
        {empresas.data?.map((e) => (
          <option key={e.id} value={e.id}>
            {e.trade_name || e.legal_name}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="flex h-10 shrink-0 items-center gap-1.5 rounded-md border px-3 text-sm font-medium hover:bg-[#edf1f2]"
        onClick={() => setCreando(true)}
      >
        <Plus className="size-4" aria-hidden="true" />
        Nuevo
      </button>
    </div>
  );
}
