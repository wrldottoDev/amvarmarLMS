"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, Info, PackageOpen } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import { Modal } from "@/components/ui/modal";
import { useSesion } from "@/features/auth/contexto-sesion";
import { metodos } from "@/features/despachos/catalogo";
import { useCrearDespacho } from "@/features/despachos/consultas";
import { api, exigirDatos } from "@/lib/api/client";
import { clases } from "@/lib/utilidades";

export default function PaginaNuevoDespacho() {
  const router = useRouter();
  const { usuario } = useSesion();
  const empresaId = usuario?.empresa?.id ?? "";

  const [seleccionadas, setSeleccionadas] = useState<string[]>([]);
  const [metodo, setMetodo] = useState<(typeof metodos)[number]["valor"]>("SEA");
  const [direccion, setDireccion] = useState("");
  const [instrucciones, setInstrucciones] = useState("");
  const [confirmando, setConfirmando] = useState(false);

  // Solo se pueden despachar cargas almacenadas. Se filtra acá en vez de
  // mostrarlas todas y rechazar al enviar: ofrecer algo que va a fallar hace
  // que la persona crea que hizo algo mal.
  const disponibles = useQuery({
    queryKey: ["cargas", "disponibles-para-despacho"],
    queryFn: async () =>
      exigirDatos(
        await api.GET("/api/v1/shipments", {
          params: { query: { limit: 100, status: ["STORED"], archived: false } },
        }),
      ),
  });

  const crear = useCrearDespacho(empresaId);

  function alternar(id: string) {
    setSeleccionadas((actuales) =>
      actuales.includes(id) ? actuales.filter((otro) => otro !== id) : [...actuales, id],
    );
  }

  async function confirmar() {
    const resultado = await crear.mutateAsync({
      method: metodo,
      shipment_ids: seleccionadas,
      delivery_address: direccion.trim() || null,
      instructions: instrucciones.trim() || null,
      requested_pickup_date: null,
    });
    router.push(`/despachos/${resultado.id}`);
  }

  const cargas = disponibles.data?.items ?? [];

  return (
    <section className="mx-auto max-w-3xl space-y-5">
      <Link
        href="/despachos"
        className="inline-flex items-center gap-1.5 text-sm text-[var(--texto-secundario)] hover:underline"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        Volver a despachos
      </Link>

      <header>
        <h1 className="text-xl font-bold">Solicitar despacho</h1>
        <p className="mt-1 text-sm text-[var(--texto-secundario)]">
          Elegí qué cargas querés que salgan de bodega y cómo. Operaciones lo revisa y te avisa.
        </p>
      </header>

      {disponibles.error ? <AvisoError error={disponibles.error} /> : null}
      {disponibles.isPending ? <CargandoPagina /> : null}

      {disponibles.data && cargas.length === 0 ? (
        <div className="rounded-md border bg-[var(--superficie)] px-6 py-14 text-center">
          <PackageOpen
            className="mx-auto size-8 text-[var(--texto-secundario)]"
            aria-hidden="true"
          />
          <p className="mt-3 text-sm font-medium">No tenés cargas listas para despachar.</p>
          <p className="mx-auto mt-1 max-w-sm text-sm text-[var(--texto-secundario)]">
            Solo se pueden despachar las cargas que ya están almacenadas en bodega. Cuando alguna
            llegue, aparece acá.
          </p>
          <Link
            href="/shipments"
            className="mt-4 inline-flex h-10 items-center rounded-md border px-4 text-sm font-semibold hover:bg-[var(--hover)]"
          >
            Ver mis cargas
          </Link>
        </div>
      ) : null}

      {cargas.length > 0 ? (
        <>
          <div className="rounded-md border bg-[var(--superficie)]">
            <div className="flex items-center justify-between border-b px-4 py-3">
              <div>
                <strong className="block text-sm">1. ¿Qué cargas querés despachar?</strong>
                <span className="text-xs text-[var(--texto-secundario)]">
                  Podés elegir varias en una sola solicitud.
                </span>
              </div>
              {cargas.length > 1 ? (
                <button
                  type="button"
                  className="text-xs font-medium text-[var(--mar)] hover:underline"
                  onClick={() =>
                    setSeleccionadas(
                      seleccionadas.length === cargas.length ? [] : cargas.map((c) => c.id),
                    )
                  }
                >
                  {seleccionadas.length === cargas.length ? "Quitar todas" : "Elegir todas"}
                </button>
              ) : null}
            </div>

            <ul className="divide-y">
              {cargas.map((carga) => {
                const elegida = seleccionadas.includes(carga.id);
                return (
                  <li key={carga.id}>
                    <label
                      className={clases(
                        "flex cursor-pointer items-center gap-3 px-4 py-3.5",
                        elegida ? "bg-[var(--marca-tenue)]" : "hover:bg-[var(--hover)]",
                      )}
                    >
                      <input
                        type="checkbox"
                        className="size-4 accent-[var(--mar)]"
                        checked={elegida}
                        onChange={() => alternar(carga.id)}
                      />
                      <span className="min-w-0 flex-1">
                        <strong className="block text-sm">{carga.shipment_number}</strong>
                        <span className="block text-xs text-[var(--texto-secundario)]">
                          {carga.package_count === 1
                            ? "1 bulto"
                            : `${carga.package_count} bultos`}
                          {carga.invoice ? ` · Factura ${carga.invoice}` : ""}
                        </span>
                      </span>
                      {carga.open_requirements_count ? (
                        <span
                          className="flex items-center gap-1 rounded-full border border-[var(--advertencia-borde)] bg-[var(--advertencia-tenue)] px-2 py-0.5 text-[11px] font-semibold text-[var(--advertencia)]"
                          title="Operaciones no puede aprobar el despacho hasta tener estos documentos."
                        >
                          <AlertTriangle className="size-3" aria-hidden="true" />
                          Faltan documentos
                        </span>
                      ) : null}
                    </label>
                  </li>
                );
              })}
            </ul>
          </div>

          <div className="rounded-md border bg-[var(--superficie)] px-4 py-4">
            <strong className="block text-sm">2. ¿Cómo querés que salga?</strong>
            <div className="mt-3 grid gap-2 sm:grid-cols-3">
              {metodos.map((opcion) => (
                <label
                  key={opcion.valor}
                  className={clases(
                    "cursor-pointer rounded-md border px-3 py-3",
                    metodo === opcion.valor
                      ? "border-[var(--mar)] bg-[var(--marca-tenue)] ring-1 ring-[var(--mar)]"
                      : "hover:bg-[var(--hover)]",
                  )}
                >
                  <span className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="metodo"
                      className="size-4 accent-[var(--mar)]"
                      checked={metodo === opcion.valor}
                      onChange={() => setMetodo(opcion.valor)}
                    />
                    <strong className="text-sm">{opcion.etiqueta}</strong>
                  </span>
                  <span className="mt-1 block pl-6 text-xs text-[var(--texto-secundario)]">
                    {opcion.ayuda}
                  </span>
                </label>
              ))}
            </div>
          </div>

          <div className="rounded-md border bg-[var(--superficie)] px-4 py-4">
            <strong className="block text-sm">3. ¿Algo más que debamos saber?</strong>
            <span className="text-xs text-[var(--texto-secundario)]">Los dos son opcionales.</span>

            <label className="mt-3 block">
              <span className="mb-1 block text-sm font-medium">Dirección de entrega</span>
              <textarea
                className="w-full rounded-md border px-3 py-2 text-sm"
                rows={2}
                value={direccion}
                onChange={(evento) => setDireccion(evento.target.value)}
                placeholder="Si va a una dirección distinta a la habitual"
              />
            </label>

            <label className="mt-3 block">
              <span className="mb-1 block text-sm font-medium">Instrucciones</span>
              <textarea
                className="w-full rounded-md border px-3 py-2 text-sm"
                rows={2}
                value={instrucciones}
                onChange={(evento) => setInstrucciones(evento.target.value)}
                placeholder="Horarios, contacto en destino, cuidados especiales"
              />
            </label>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-[var(--superficie)] px-4 py-4">
            <p className="text-sm">
              {seleccionadas.length === 0 ? (
                <span className="text-[var(--texto-secundario)]">
                  Elegí al menos una carga para continuar.
                </span>
              ) : (
                <>
                  <strong>
                    {seleccionadas.length === 1 ? "1 carga" : `${seleccionadas.length} cargas`}
                  </strong>{" "}
                  · {metodos.find((m) => m.valor === metodo)?.etiqueta}
                </>
              )}
            </p>
            <Boton onClick={() => setConfirmando(true)} disabled={seleccionadas.length === 0}>
              Revisar y enviar
            </Boton>
          </div>
        </>
      ) : null}

      <Modal
        abierto={confirmando}
        titulo="¿Confirmás la solicitud?"
        cerrar={() => setConfirmando(false)}
      >
        <div className="space-y-3 text-sm">
          <p>
            Vas a pedir el despacho de{" "}
            <strong>
              {seleccionadas.length === 1 ? "1 carga" : `${seleccionadas.length} cargas`}
            </strong>{" "}
            por vía <strong>{metodos.find((m) => m.valor === metodo)?.etiqueta.toLowerCase()}</strong>.
          </p>

          {/* ADR-0013: el cliente solo cancela antes de la aprobación. Se dice
              acá, cuando todavía puede echarse atrás, y no después. */}
          <div className="flex gap-2 rounded-md border border-[var(--advertencia-borde)] bg-[var(--advertencia-tenue)] px-3 py-2.5 text-[var(--advertencia)]">
            <Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <p>
              Podés cancelarla mientras Operaciones no la haya aprobado. Después ya no, porque
              puede haber transporte contratado.
            </p>
          </div>

          {crear.error ? <AvisoError error={crear.error} /> : null}

          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              className="h-10 rounded-md border px-4 text-sm font-medium hover:bg-[var(--hover)]"
              onClick={() => setConfirmando(false)}
            >
              Volver
            </button>
            <Boton onClick={() => void confirmar()} cargando={crear.isPending}>
              Sí, enviar solicitud
            </Boton>
          </div>
        </div>
      </Modal>
    </section>
  );
}
