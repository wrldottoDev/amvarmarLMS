"use client";

import { Building2, Plus, Power, Users } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import { Modal } from "@/components/ui/modal";
import {
  useCrearEmpresa,
  useDesactivarEmpresa,
  useEmpresas,
} from "@/features/admin/consultas";
import { etiquetaEstadoCuenta } from "@/features/admin/roles";
import { clases, formatearFecha } from "@/lib/utilidades";

export default function PaginaEmpresas() {
  const [incluirInactivas, setIncluirInactivas] = useState(false);
  const [creando, setCreando] = useState(false);
  const [aDesactivar, setADesactivar] = useState<{ id: string; nombre: string } | null>(null);

  const { data, isPending, error } = useEmpresas(incluirInactivas);
  const crear = useCrearEmpresa();
  const desactivar = useDesactivarEmpresa();

  const [nombre, setNombre] = useState("");
  const [comercial, setComercial] = useState("");
  const [cedula, setCedula] = useState("");

  async function guardar() {
    await crear.mutateAsync({
      legal_name: nombre.trim(),
      trade_name: comercial.trim() || null,
      tax_id: cedula.trim() || null,
    });
    setNombre("");
    setComercial("");
    setCedula("");
    setCreando(false);
  }

  return (
    <section className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">Empresas</h1>
          <p className="mt-0.5 text-sm text-[var(--texto-secundario)]">
            Los clientes de AMVARMAR y las personas que los manejan.
          </p>
        </div>
        <Boton onClick={() => setCreando(true)}>
          <Plus className="size-4" aria-hidden="true" />
          Nueva empresa
        </Boton>
      </header>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="size-4 accent-[var(--mar)]"
          checked={incluirInactivas}
          onChange={(evento) => setIncluirInactivas(evento.target.checked)}
        />
        Mostrar también las desactivadas
      </label>

      {error ? <AvisoError error={error} /> : null}
      {isPending ? <CargandoPagina /> : null}

      {data && data.length > 0 ? (
        <ul className="grid gap-3">
          {data.map((empresa) => (
            <li
              key={empresa.id}
              className={clases(
                "flex flex-wrap items-center gap-4 rounded-md border bg-[var(--superficie)] px-4 py-4",
                empresa.status !== "ACTIVE" && "opacity-70",
              )}
            >
              <span className="grid size-10 shrink-0 place-items-center rounded-md bg-[var(--marca-tenue)] text-[var(--mar)]">
                <Building2 className="size-5" aria-hidden="true" />
              </span>

              <span className="min-w-0 flex-1">
                <strong className="block text-sm">
                  {empresa.trade_name || empresa.legal_name}
                </strong>
                <span className="block text-xs text-[var(--texto-secundario)]">
                  {empresa.trade_name ? `${empresa.legal_name} · ` : ""}
                  {empresa.tax_id || "Sin cédula"} · Cliente desde{" "}
                  {formatearFecha(empresa.created_at)}
                </span>
              </span>

              <span className="flex shrink-0 gap-4 text-center">
                <span>
                  <strong className="block text-lg tabular-nums">{empresa.usuarios}</strong>
                  <span className="text-[11px] text-[var(--texto-secundario)]">usuarios</span>
                </span>
                <span>
                  <strong className="block text-lg tabular-nums">{empresa.cargas}</strong>
                  <span className="text-[11px] text-[var(--texto-secundario)]">cargas</span>
                </span>
              </span>

              {empresa.status !== "ACTIVE" ? (
                <span className="shrink-0 rounded-full border border-[var(--peligro-borde)] bg-[var(--peligro-tenue)] px-2.5 py-0.5 text-xs font-semibold text-[var(--peligro)]">
                  {etiquetaEstadoCuenta[empresa.status] ?? empresa.status}
                </span>
              ) : null}

              <span className="flex shrink-0 gap-2">
                <Link
                  href={`/usuarios?empresa=${empresa.id}`}
                  className="flex h-9 items-center gap-1.5 rounded-md border px-3 text-sm font-medium hover:bg-[var(--hover)]"
                >
                  <Users className="size-4" aria-hidden="true" />
                  Usuarios
                </Link>
                {empresa.status === "ACTIVE" ? (
                  <button
                    type="button"
                    className="flex h-9 items-center gap-1.5 rounded-md border px-3 text-sm font-medium text-[var(--peligro)] hover:bg-[var(--peligro-tenue)]"
                    onClick={() =>
                      setADesactivar({
                        id: empresa.id,
                        nombre: empresa.trade_name || empresa.legal_name,
                      })
                    }
                  >
                    <Power className="size-4" aria-hidden="true" />
                    Desactivar
                  </button>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {data && data.length === 0 ? (
        <div className="rounded-md border bg-[var(--superficie)] px-6 py-16 text-center">
          <Building2 className="mx-auto size-8 text-[var(--texto-secundario)]" aria-hidden="true" />
          <p className="mt-3 text-sm font-medium">Todavía no hay empresas.</p>
          <p className="mt-1 text-sm text-[var(--texto-secundario)]">
            Creá la primera para poder darle cargas y usuarios.
          </p>
        </div>
      ) : null}

      <Modal abierto={creando} titulo="Nueva empresa" cerrar={() => setCreando(false)}>
        <div className="space-y-3">
          <label className="block">
            <span className="mb-1 block text-sm font-medium">Nombre legal</span>
            <input
              className="w-full rounded-md border px-3 py-2 text-sm"
              value={nombre}
              onChange={(evento) => setNombre(evento.target.value)}
              placeholder="Importaciones Ejemplo S.A."
            />
          </label>

          <label className="block">
            <span className="mb-1 block text-sm font-medium">
              Nombre comercial <span className="font-normal text-[var(--texto-secundario)]">(opcional)</span>
            </span>
            <input
              className="w-full rounded-md border px-3 py-2 text-sm"
              value={comercial}
              onChange={(evento) => setComercial(evento.target.value)}
              placeholder="Cómo se les conoce"
            />
          </label>

          <label className="block">
            <span className="mb-1 block text-sm font-medium">
              Cédula jurídica <span className="font-normal text-[var(--texto-secundario)]">(opcional)</span>
            </span>
            <input
              className="w-full rounded-md border px-3 py-2 text-sm"
              value={cedula}
              onChange={(evento) => setCedula(evento.target.value)}
              placeholder="3-101-000000"
            />
          </label>

          {crear.error ? <AvisoError error={crear.error} /> : null}

          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              className="h-10 rounded-md border px-4 text-sm font-medium hover:bg-[var(--hover)]"
              onClick={() => setCreando(false)}
            >
              Cancelar
            </button>
            <Boton onClick={() => void guardar()} disabled={!nombre.trim()} cargando={crear.isPending}>
              Crear empresa
            </Boton>
          </div>
        </div>
      </Modal>

      <Modal
        abierto={aDesactivar !== null}
        titulo={`¿Desactivar ${aDesactivar?.nombre ?? ""}?`}
        cerrar={() => setADesactivar(null)}
      >
        <div className="space-y-3 text-sm">
          <p>
            Sus usuarios quedan suspendidos y no van a poder entrar. Las cargas, documentos y el
            historial <strong>no se borran</strong>: siguen ahí para consulta.
          </p>
          {desactivar.error ? <AvisoError error={desactivar.error} /> : null}
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              className="h-10 rounded-md border px-4 text-sm font-medium hover:bg-[var(--hover)]"
              onClick={() => setADesactivar(null)}
            >
              No, volver
            </button>
            <Boton
              variante="peligro"
              cargando={desactivar.isPending}
              onClick={async () => {
                if (aDesactivar) await desactivar.mutateAsync(aDesactivar.id);
                setADesactivar(null);
              }}
            >
              Sí, desactivar
            </Boton>
          </div>
        </div>
      </Modal>
    </section>
  );
}
