"use client";

import { ArrowLeft, PackagePlus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { SelectorEmpresa } from "@/components/admin/selector-empresa";
import {
  aPayload,
  EditorPiezas,
  type Pieza,
  piezaVacia,
  problemaDePiezas,
} from "@/components/shipments/editor-piezas";
import {
  EditorPeso,
  pesoParaApi,
  pesoValido,
  pesoVacio,
  type PesoEditable,
} from "@/components/shipments/editor-peso";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import { useBodegas, useCrearCarga, useEmpresas, useUbicaciones } from "@/features/admin/consultas";
import { useSesion } from "@/features/auth/contexto-sesion";
import { clases } from "@/lib/utilidades";

const ESTADOS_INICIALES = [
  { valor: "PRE_ALERT", etiqueta: "Prealerta — todavía no llegó" },
  { valor: "IN_TRANSIT", etiqueta: "En tránsito — va en camino" },
  { valor: "RECEIVED", etiqueta: "Recibida — llegó, sin contar" },
  { valor: "STORED", etiqueta: "Almacenada — está en bodega y contada" },
] as const;

export default function PaginaNuevaCarga() {
  const router = useRouter();
  const { usuario, estado: estadoSesion } = useSesion();
  const esCliente = Boolean(usuario?.empresa);
  const empresas = useEmpresas(false, estadoSesion === "autenticada" && !esCliente);
  const ubicaciones = useUbicaciones();
  const bodegas = useBodegas();
  const crear = useCrearCarga();

  // La pregunta que decide todo lo demás. `null` = todavía sin responder: no se
  // muestra el resto del formulario hasta que se elija, porque de la respuesta
  // depende si la carga se identifica por WR o por factura.
  const [desdeMiami, setDesdeMiami] = useState<boolean | null>(null);

  const [empresa, setEmpresa] = useState("");
  const [origen, setOrigen] = useState("");
  const [destino, setDestino] = useState("");
  const [estado, setEstado] = useState<string>("PRE_ALERT");
  const [descripcion, setDescripcion] = useState("");
  const [direccion, setDireccion] = useState("");
  const [eta, setEta] = useState("");
  const [peso, setPeso] = useState<PesoEditable>(pesoVacio);
  const [pesoVol, setPesoVol] = useState("");
  const [cft, setCft] = useState("");
  const [transporte, setTransporte] = useState<"SEA" | "AIR" | "LAND">("SEA");
  const [wr, setWr] = useState("");
  const [factura, setFactura] = useState("");
  const [shipper, setShipper] = useState("");
  const [carrier, setCarrier] = useState("");
  const [contenedor, setContenedor] = useState("");
  const [tracking, setTracking] = useState("");
  const [orden, setOrden] = useState("");
  const [permiso, setPermiso] = useState(false);
  // Nace con una fila. Un formulario que empieza vacío y rechaza el guardado
  // por eso le hace perder el trabajo a quien ya llenó todo lo demás.
  const [piezas, setPiezas] = useState<Pieza[]>(() => [piezaVacia()]);

  if (
    estadoSesion !== "autenticada" ||
    (!esCliente && empresas.isPending) ||
    ubicaciones.isPending ||
    bodegas.isPending
  ) {
    return <CargandoPagina />;
  }

  // La bodega que emite Warehouse Receipt. Se busca por la bandera y no por el
  // código "MIA": si mañana abren otra bodega que emita WR, esto la encuentra
  // sola en vez de quedar mintiendo.
  const bodegaConWr = (bodegas.data ?? []).find((b) => b.uses_warehouse_receipt);

  const origenEfectivo = desdeMiami ? (bodegaConWr?.location_id ?? "") : origen;
  const bodegaEfectiva = desdeMiami ? (bodegaConWr?.id ?? null) : null;
  const empresaEfectiva = usuario?.empresa?.id ?? empresa;

  // La regla del negocio: lo que sale de una bodega que emite WR se identifica
  // por el WR; todo lo demás, por su factura.
  const exigeFactura = desdeMiami === false;
  const hayPeso = pesoValido(peso);

  const listo = Boolean(
    empresaEfectiva &&
      origenEfectivo &&
      destino &&
      origenEfectivo !== destino &&
      hayPeso &&
      problemaDePiezas(piezas) === null &&
      (!exigeFactura || factura.trim()),
  );

  async function guardar() {
    const creada = await crear.mutateAsync({
      company_id: empresaEfectiva,
      origin_location_id: origenEfectivo,
      destination_location_id: destino,
      origin_facility_id: bodegaEfectiva,
      initial_status: estado,
      destination_address: direccion.trim() || null,
      description: descripcion.trim() || null,
      estimated_arrival_at: eta ? new Date(`${eta}T12:00:00`).toISOString() : null,
      weight: pesoParaApi(peso)!,
      volumetric_weight_kg: pesoVol || null,
      foots_cft: cft || null,
      transport_mode: transporte,
      shipper: shipper.trim() || null,
      carrier: carrier.trim() || null,
      wr: desdeMiami ? wr.trim() || null : null,
      invoice: factura.trim() || null,
      container: contenedor.trim() || null,
      tracking: tracking.trim() || null,
      po: orden.trim() || null,
      permit_review_required: permiso,
      packages: aPayload(piezas),
    });
    // Al expediente, no al detalle. Es el flujo del sistema viejo:
    // `create_warehouse` guardaba y redirigía a `warehouse_files`, porque quien
    // acaba de dar de alta una carga casi siempre tiene los papeles en la mano.
    router.push(`/cargas/${creada.id}/archivos`);
  }

  return (
    <section className="mx-auto max-w-4xl space-y-4">
      <Link
        href="/shipments"
        className="inline-flex items-center gap-1.5 text-sm text-[var(--texto-secundario)] hover:underline"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        Volver a cargas
      </Link>

      <h1 className="text-2xl font-bold text-[var(--mar)]">Nueva carga</h1>

      {/* La pregunta de arranque. Va primero y sola porque de ella depende cómo
          se identifica la carga, y responderla al final obligaría a rehacer lo
          que ya se escribió. */}
      <div className="rounded-lg border bg-[var(--superficie)]">
        <div className="border-b px-4 py-2.5 font-semibold">¿De dónde sale la carga?</div>
        <div className="grid gap-3 p-4 sm:grid-cols-2">
          <button
            type="button"
            onClick={() => setDesdeMiami(true)}
            aria-pressed={desdeMiami === true}
            className={clases(
              "rounded-lg border-2 px-4 py-3 text-left transition-colors",
              desdeMiami === true
                ? "border-[var(--marca)] bg-[var(--marca-tenue)]"
                : "hover:bg-[var(--hover)]",
            )}
          >
            <strong className="block">De nuestra bodega de Miami</strong>
            <span className="text-sm text-[var(--texto-secundario)]">
              Lleva Warehouse Receipt. La bodega lo emite al recibir la mercancía.
            </span>
          </button>

          <button
            type="button"
            onClick={() => setDesdeMiami(false)}
            aria-pressed={desdeMiami === false}
            className={clases(
              "rounded-lg border-2 px-4 py-3 text-left transition-colors",
              desdeMiami === false
                ? "border-[var(--marca)] bg-[var(--marca-tenue)]"
                : "hover:bg-[var(--hover)]",
            )}
          >
            <strong className="block">De otro origen</strong>
            <span className="text-sm text-[var(--texto-secundario)]">
              No lleva WR. Se identifica por su factura, que es obligatoria.
            </span>
          </button>
        </div>

        {desdeMiami === true && !bodegaConWr ? (
          <p className="border-t px-4 py-3 text-sm text-[var(--peligro)]">
            No hay ninguna bodega configurada para emitir Warehouse Receipt. Configurala en
            Inventario antes de dar de alta cargas de Miami.
          </p>
        ) : null}
      </div>

      {desdeMiami === null ? (
        <p className="rounded-lg border border-dashed px-4 py-10 text-center text-sm text-[var(--texto-secundario)]">
          Elegí el origen para continuar.
        </p>
      ) : (
        <>
          <div className="space-y-4 rounded-lg border bg-[var(--superficie)] p-4">
            <div className="font-semibold">Datos de la carga</div>

            <div className="block">
              <span className="mb-1 block text-sm font-medium">¿De qué cliente es?</span>
              {esCliente ? (
                <p className="rounded-md border bg-[var(--hover)] px-3 py-2 text-sm">
                  {usuario?.empresa?.trade_name || usuario?.empresa?.legal_name}
                </p>
              ) : (
                <SelectorEmpresa valor={empresa} alCambiar={setEmpresa} />
              )}
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1 block text-sm font-medium">Sale de</span>
                {desdeMiami ? (
                  <p className="rounded-md border bg-[var(--hover)] px-3 py-2 text-sm">
                    {ubicaciones.data?.find((u) => u.id === origenEfectivo)?.name ?? "Miami"}
                    <span className="text-[var(--texto-secundario)]">
                      {" "}
                      · bodega {bodegaConWr?.facility_code}
                    </span>
                  </p>
                ) : (
                  <select
                    className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
                    value={origen}
                    onChange={(evento) => setOrigen(evento.target.value)}
                  >
                    <option value="">Origen…</option>
                    {ubicaciones.data?.map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.name} ({u.location_code})
                      </option>
                    ))}
                  </select>
                )}
              </label>

              <label className="block">
                <span className="mb-1 block text-sm font-medium">Llega a</span>
                <select
                  className={clases(
                    "w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm",
                    origenEfectivo && destino && origenEfectivo === destino && "border-[var(--peligro)]",
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
                {origenEfectivo && destino && origenEfectivo === destino ? (
                  <span className="mt-1 block text-xs text-[var(--peligro)]">
                    El origen y el destino no pueden ser el mismo lugar.
                  </span>
                ) : null}
              </label>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              {desdeMiami ? (
                <label className="block">
                  <span className="mb-1 block text-sm font-medium">
                    Warehouse Receipt{" "}
                    <span className="font-normal text-[var(--texto-secundario)]">
                      (si ya lo emitieron)
                    </span>
                  </span>
                  <input
                    className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
                    value={wr}
                    onChange={(evento) => setWr(evento.target.value)}
                    placeholder="WR105921"
                  />
                  <span className="mt-1 block text-xs text-[var(--texto-secundario)]">
                    Se puede dejar vacío: se exige recién al dar la carga por almacenada.
                  </span>
                </label>
              ) : null}

              <label className="block">
                <span className="mb-1 block text-sm font-medium">
                  Factura
                  {exigeFactura ? (
                    <span className="font-normal text-[var(--peligro)]"> — obligatoria</span>
                  ) : (
                    <span className="font-normal text-[var(--texto-secundario)]"> (opcional)</span>
                  )}
                </span>
                <input
                  className={clases(
                    "w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm",
                    exigeFactura && !factura.trim() && "border-[var(--peligro)]",
                  )}
                  value={factura}
                  onChange={(evento) => setFactura(evento.target.value)}
                />
              </label>
            </div>

            {esCliente ? (
              <p className="rounded-md border bg-[var(--hover)] px-3 py-2 text-sm">
                La carga se registra como prealerta. Operaciones actualizará su avance.
              </p>
            ) : (
              <label className="block">
                <span className="mb-1 block text-sm font-medium">Estado en que se registra</span>
                <select
                  className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
                  value={estado}
                  onChange={(evento) => setEstado(evento.target.value)}
                >
                  {ESTADOS_INICIALES.map((e) => (
                    <option key={e.valor} value={e.valor}>
                      {e.etiqueta}
                    </option>
                  ))}
                </select>
              </label>
            )}

            <label className="block">
              <span className="mb-1 block text-sm font-medium">
                ¿Qué viene?{" "}
                <span className="font-normal text-[var(--texto-secundario)]">(opcional)</span>
              </span>
              <textarea
                className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
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
                  className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
                  value={eta}
                  onChange={(evento) => setEta(evento.target.value)}
                />
              </label>

              <label className="block">
                <span className="mb-1 block text-sm font-medium">
                  Dirección de entrega{" "}
                  <span className="font-normal text-[var(--texto-secundario)]">(opcional)</span>
                </span>
                <input
                  className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
                  value={direccion}
                  onChange={(evento) => setDireccion(evento.target.value)}
                />
              </label>
            </div>
          </div>

          <fieldset className="space-y-3 rounded-lg border bg-[var(--superficie)] p-4">
            <legend className="px-1 font-semibold">Peso y volumen</legend>
            <EditorPeso valor={peso} alCambiar={setPeso} mostrarError={!hayPeso} />

            <div className="grid gap-3 sm:grid-cols-2">
              {(
                [
                  ["Volumétrico (kg)", pesoVol, setPesoVol],
                  ["Pies cúbicos (CFT)", cft, setCft],
                ] as const
              ).map(([etiqueta, valor, asignar]) => (
                <label className="block" key={etiqueta}>
                  <span className="mb-1 block text-sm font-medium">{etiqueta}</span>
                  <input
                    type="number"
                    min="0"
                    step="0.001"
                    className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
                    value={valor}
                    onChange={(evento) => asignar(evento.target.value)}
                  />
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset className="space-y-3 rounded-lg border bg-[var(--superficie)] p-4">
            <legend className="px-1 font-semibold">Datos comerciales</legend>

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1 block text-sm font-medium">Método de transporte</span>
                <select
                  className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
                  value={transporte}
                  onChange={(evento) =>
                    setTransporte(evento.target.value as "SEA" | "AIR" | "LAND")
                  }
                >
                  <option value="SEA">Marítimo</option>
                  <option value="AIR">Aéreo</option>
                  <option value="LAND">Terrestre</option>
                </select>
              </label>
              {(
                [
                  ["Shipper", shipper, setShipper, "Quién envía la mercancía"],
                  ["Carrier", carrier, setCarrier, "Naviera o aerolínea"],
                  ["Contenedor", contenedor, setContenedor, "MSKU1234567"],
                  ["Tracking", tracking, setTracking, ""],
                  ["Orden de compra (PO)", orden, setOrden, ""],
                ] as const
              ).map(([etiqueta, valor, asignar, ejemplo]) => (
                <label className="block" key={etiqueta}>
                  <span className="mb-1 block text-sm font-medium">{etiqueta}</span>
                  <input
                    className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
                    value={valor}
                    onChange={(evento) => asignar(evento.target.value)}
                    placeholder={ejemplo}
                  />
                </label>
              ))}
            </div>
          </fieldset>

          <EditorPiezas piezas={piezas} alCambiar={setPiezas} />

          <label className="flex items-start gap-2 rounded-lg border border-[var(--advertencia-borde)] bg-[var(--advertencia-tenue)] px-4 py-3">
            <input
              type="checkbox"
              className="mt-0.5 size-4"
              checked={permiso}
              onChange={(evento) => setPermiso(evento.target.checked)}
            />
            <span className="text-sm text-[var(--advertencia)]">
              <strong className="block">Puede requerir permiso o inspección</strong>
              Marcalo si la mercancía es regulada. Le vamos a pedir el permiso especial al cliente
              antes de despachar.
            </span>
          </label>

          {crear.error ? <AvisoError error={crear.error} /> : null}

          <div className="flex justify-end gap-2 pb-4">
            <Link
              href="/shipments"
              className="flex h-10 items-center rounded-md border px-4 text-sm font-medium hover:bg-[var(--hover)]"
            >
              Cancelar
            </Link>
            <Boton onClick={() => void guardar()} disabled={!listo} cargando={crear.isPending}>
              <PackagePlus className="size-4" aria-hidden="true" />
              Crear carga
            </Boton>
          </div>
        </>
      )}
    </section>
  );
}
