"use client";

import { useQuery } from "@tanstack/react-query";
import { metodos } from "@/features/despachos/catalogo";
import {
  Archive,
  ArrowLeft,
  ArrowRight,
  CalendarDays,
  FileText,
  History,
  LayoutList,
  Package,
  Pencil,
  Route,
  Ship,
  Tag,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { BadgeEstado, BadgePendientes } from "@/components/shipments/badges-carga";
import { TimelineCarga } from "@/components/shipments/timeline-carga";
import { TransicionCarga } from "@/components/shipments/transicion-carga";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { Expediente } from "@/components/documentos/expediente";
import { EstadoExplicado } from "@/components/shipments/estado-explicado";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import { useSesion } from "@/features/auth/contexto-sesion";
import { identificadorCarga } from "@/features/shipments/identificador";
import { ubicacionParaCliente } from "@/features/shipments/vocabulario";
import { useActualizarDatosTransito } from "@/features/shipments/consultas";
import { api, exigirDatos } from "@/lib/api/client";
import { formatearFecha, formatearFechaHora } from "@/lib/utilidades";

/**
 * Estados en los que la carga todavía se puede corregir.
 *
 * Es el mismo conjunto que `_EDITABLES` de `shipments/gestion.py`. Una vez
 * despachada, un error se registra como corrección en la línea de tiempo y no
 * se sobreescribe: el expediente ya salió con esos datos.
 */
const EDITABLES = new Set([
  "PRE_ALERT",
  "BOOKING_ASSIGNED",
  "IN_TRANSIT",
  "TRANSSHIPMENT",
  "AT_DESTINATION",
  "RECEIVED",
  "STORED",
]);

/** Los mismos nombres que usaba el desplegable del sistema viejo. */
const ETIQUETA_PIEZA: Record<string, string> = {
  PALLET: "Pallets",
  BOX: "Cajas",
  DRUM: "Tambores",
  BUNDLE: "Bultos",
  OTHER: "Otro",
};

function Dato({ etiqueta, valor }: { etiqueta: string; valor: React.ReactNode }) {
  return (
    <div className="min-w-0 py-4">
      <dt className="text-xs font-bold uppercase text-[var(--texto-secundario)]">{etiqueta}</dt>
      <dd className="mt-1.5 text-sm font-medium">{valor || "No registrado"}</dd>
    </div>
  );
}

function FormularioDatosTransito({
  cargaId,
  rowVersion,
  descripcionInicial,
  partidaInicial,
}: {
  cargaId: string;
  rowVersion: number;
  descripcionInicial: string | null;
  partidaInicial: string | null;
}) {
  const [descripcion, setDescripcion] = useState(descripcionInicial ?? "");
  const [partida, setPartida] = useState(partidaInicial ?? "");
  const actualizar = useActualizarDatosTransito(cargaId);
  const faltanDatos = !descripcionInicial || !partidaInicial;

  return (
    <div className="mt-5 space-y-4 rounded-lg border bg-[var(--superficie)] p-4">
      {faltanDatos ? (
        <p className="rounded-md border border-[var(--advertencia-borde)] bg-[var(--advertencia-tenue)] p-3 text-sm">
          Por favor, ayúdenos agregando la descripción en español de lo que viene y la partida
          arancelaria.
        </p>
      ) : null}
      <label className="block">
        <span className="mb-1 block text-sm font-medium">Descripción en español</span>
        <textarea
          className="min-h-28 w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
          value={descripcion}
          maxLength={4000}
          onChange={(evento) => setDescripcion(evento.target.value)}
          placeholder="Describa en español la mercancía que viene"
        />
      </label>
      <label className="block">
        <span className="mb-1 block text-sm font-medium">Partida arancelaria</span>
        <input
          className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
          value={partida}
          inputMode="numeric"
          maxLength={40}
          onChange={(evento) => setPartida(evento.target.value.replace(/\D/g, "").slice(0, 40))}
          placeholder="6 u 8 dígitos (se permiten hasta 40)"
        />
      </label>
      {actualizar.error ? <AvisoError error={actualizar.error} /> : null}
      {actualizar.isSuccess ? (
        <p className="text-sm font-medium text-[var(--exito)]">Datos guardados y enviados a administración.</p>
      ) : null}
      <div className="flex justify-end">
        <Boton
          cargando={actualizar.isPending}
          disabled={!descripcion.trim() && !partida.trim()}
          onClick={() =>
            actualizar.mutate({
              row_version: rowVersion,
              description: descripcion.trim() || null,
              tariff_code: partida.trim() || null,
            })
          }
        >
          Guardar datos
        </Boton>
      </div>
    </div>
  );
}

export default function PaginaDetalleCarga() {
  const parametros = useParams<{ id: string }>();
  const cargaId = parametros.id;
  const { usuario } = useSesion();
  const [vista, setVista] = useState<"resumen" | "documentos" | "historial">("resumen");
  const consulta = useQuery({
    queryKey: ["carga", cargaId],
    queryFn: async () =>
      exigirDatos(
        await api.GET("/api/v1/shipments/{shipment_id}", {
          params: { path: { shipment_id: cargaId } },
        }),
      ),
  });

  if (consulta.isLoading) return <CargandoPagina texto="Cargando carga" />;
  if (consulta.error) return <AvisoError error={consulta.error} />;
  if (!consulta.data) return null;

  const carga = consulta.data;
  const esCliente = Boolean(usuario?.empresa);
  const esTransito = carga.origin_kind === "TRANSIT";
  const ocultarDatosLogisticos = esCliente && esTransito;
  const pendientes = usuario?.empresa ? carga.client_action_required_count : carga.open_requirements_count;
  const identificador = identificadorCarga(carga);

  // Peso y volumen en una sola línea: son la misma pregunta —cuánto ocupa— y
  // separarlos en tres filas medio vacías no ayuda a leerlo.
  const medidas = [
    carga.weight_kg ? `${carga.weight_kg} kg` : null,
    carga.weight_lb ? `${carga.weight_lb} lb` : null,
    carga.volume_m3 ? `${carga.volume_m3} m³` : null,
    carga.foots_cft ? `${carga.foots_cft} CFT` : null,
  ].filter(Boolean);

  const tieneDatosComerciales = Boolean(
    carga.shipper ||
      carga.amvar ||
      (!ocultarDatosLogisticos &&
        (carga.carrier || carga.container || carga.tracking || carga.po)) ||
      medidas.length > 0,
  );

  return (
    <div className="space-y-8">
      <header className="border-b pb-6">
        <Link
          href={carga.origin_kind === "TRANSIT" ? "/transito" : "/miami"}
          className="mb-5 inline-flex items-center gap-2 text-sm font-semibold text-[var(--mar)] hover:underline"
        >
          <ArrowLeft className="size-4" aria-hidden="true" />
          {carga.origin_kind === "TRANSIT" ? "Reportes de tránsito" : "Miami"}
        </Link>
        <div className="flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <BadgeEstado
                estado={carga.status}
                ubicacion={esCliente ? ubicacionParaCliente(carga) : undefined}
              />
              <BadgePendientes
                cantidad={pendientes}
                etiqueta={esCliente && !esTransito ? "sugerido" : "pendiente"}
              />
              {carga.archived_at ? (
                <span
                  className="inline-flex min-h-7 items-center gap-1.5 rounded bg-[var(--hover)] px-2.5 py-1 text-xs font-bold text-[var(--texto-secundario)]"
                  title={`Archivada el ${formatearFecha(carga.archived_at)}`}
                >
                  <Archive className="size-3.5" aria-hidden="true" />
                  Archivada
                </span>
              ) : null}
            </div>
            {/* WR para Miami; BL para reportes de tránsito. */}
            <h1 className="mt-3 text-2xl font-bold sm:text-3xl">{identificador}</h1>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {/* Solo Operaciones edita, y solo mientras la carga no salió de
                bodega. Un cliente que ve el botón y recibe un 403 al pulsarlo
                aprende menos que uno que no lo ve. */}
            {!usuario?.empresa && !carga.archived_at && EDITABLES.has(carga.status) ? (
              <Link
                href={`/cargas/${carga.id}/editar`}
                className="flex h-10 items-center gap-1.5 rounded-md border px-4 text-sm font-medium hover:bg-[var(--hover)]"
              >
                <Pencil className="size-4" aria-hidden="true" />
                Editar
              </Link>
            ) : null}
            {/* Ya hay un `BadgeEstado` fijo arriba; una carga archivada no
                necesita otro, solo pierde el control de cambio de estado. */}
            {carga.archived_at ? null : (
              <TransicionCarga cargaId={carga.id} estado={carga.status} rowVersion={carga.row_version} />
            )}
          </div>
        </div>

        <div className="mt-5">
          <EstadoExplicado
            estado={carga.status}
            esCliente={Boolean(usuario?.empresa)}
            puedeDespachar={pendientes === 0}
          />
        </div>
      </header>

      <nav className="flex overflow-x-auto border-b" aria-label="Secciones de la carga">
        {(
          [
            ["resumen", "Resumen", LayoutList],
            ["documentos", "Documentos", FileText],
            ["historial", "Historial", History],
          ] as const
        ).map(([valor, etiqueta, Icono]) => (
          <button
            key={valor}
            type="button"
            onClick={() => setVista(valor)}
            aria-current={vista === valor ? "page" : undefined}
            className={`flex h-11 shrink-0 items-center gap-2 border-b-2 px-4 text-sm font-semibold ${
              vista === valor
                ? "border-[var(--marca)] text-[var(--mar)]"
                : "border-transparent text-[var(--texto-secundario)] hover:text-[var(--texto)]"
            }`}
          >
            <Icono className="size-4" aria-hidden="true" />
            {etiqueta}
          </button>
        ))}
      </nav>

      {vista === "documentos" ? (
        <Expediente
          cargaId={carga.id}
          esCliente={Boolean(usuario?.empresa)}
          soloLectura={Boolean(carga.archived_at)}
        />
      ) : null}

      {vista === "resumen" ? (
        <>
          <section aria-labelledby="ruta-carga">
        <div className="mb-4 flex items-center gap-2">
          <Route className="size-5 text-[var(--marca)]" aria-hidden="true" />
          <h2 id="ruta-carga" className="text-base font-bold">Ruta</h2>
        </div>
        <div className="grid overflow-hidden rounded-lg border bg-[var(--superficie)] md:grid-cols-[1fr_auto_1fr]">
          <div className="p-5">
            <p className="text-xs font-bold uppercase text-[var(--texto-secundario)]">Origen</p>
            <p className="mt-2 text-lg font-semibold">{carga.origin.name}</p>
            <p className="mt-1 text-sm text-[var(--texto-secundario)]">{carga.origin.location_code} · {carga.origin.country_code}</p>
          </div>
          <div className="grid place-items-center border-y px-5 py-3 text-[var(--mar)] md:border-x md:border-y-0">
            <ArrowRight className="size-5 rotate-90 md:rotate-0" aria-hidden="true" />
          </div>
          <div className="p-5">
            <p className="text-xs font-bold uppercase text-[var(--texto-secundario)]">Destino</p>
            <p className="mt-2 text-lg font-semibold">{carga.destination.name}</p>
            <p className="mt-1 text-sm text-[var(--texto-secundario)]">{carga.destination.location_code} · {carga.destination.country_code}</p>
          </div>
        </div>
          </section>

          <section className="grid gap-8 xl:grid-cols-2">
        <div>
          <h2 className="border-b pb-3 text-base font-bold">Datos de la carga</h2>
          <dl className="grid grid-cols-2 divide-x border-b">
            <div className="pr-5">
              <Dato etiqueta="ETA" valor={<span className="inline-flex items-center gap-2"><CalendarDays className="size-4 text-[var(--marca)]" />{formatearFecha(carga.estimated_arrival_at)}</span>} />
              <Dato etiqueta="Dirección de destino" valor={carga.destination_address} />
            </div>
            <div className="pl-5">
              <Dato etiqueta="Modo de transporte" valor={<span className="inline-flex items-center gap-2"><Ship className="size-4 text-[var(--marca)]" />{metodos.find((m) => m.valor === carga.transport_mode)?.etiqueta ?? carga.transport_mode ?? "No registrado"}{carga.load_type ? ` · ${carga.load_type}` : ""}</span>} />
              <Dato etiqueta="Paquetes" valor={<span className="inline-flex items-center gap-2"><Package className="size-4 text-[var(--marca)]" />{carga.package_count}</span>} />
              <Dato
                etiqueta={carga.wr ? "Warehouse Receipt" : carga.bl ? "Número de BL" : "Factura"}
                valor={
                  <span className="inline-flex items-center gap-2">
                    <Tag className="size-4 text-[var(--marca)]" />
                    {carga.wr ?? carga.bl ?? carga.invoice ?? "No registrada"}
                  </span>
                }
              />
            </div>
          </dl>
          {!esTransito ? (
            <div className="mt-5 rounded-lg border bg-[var(--superficie)] p-4">
              <h3 className="text-sm font-bold uppercase tracking-wide text-[var(--texto-secundario)]">Descripción</h3>
              <p className="mt-2 text-sm leading-6">{carga.description || "No registrada"}</p>
            </div>
          ) : null}

          {/*
            Los datos comerciales viven en su propio bloque y solo aparecen si hay
            alguno. Muchas cargas —sobre todo las migradas— no traen ninguno, y una
            rejilla de seis "No registrado" no dice nada y entierra lo que sí importa.
          */}
          {carga.packages.length > 0 ? (
            <>
              <h3 className="mt-7 border-b pb-3 text-sm font-bold uppercase tracking-wide text-[var(--texto-secundario)]">
                Piezas
              </h3>
              <table className="w-full border-b text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-[var(--texto-secundario)]">
                    <th className="py-2 font-semibold">Tipo</th>
                    <th className="py-2 font-semibold">Cantidad</th>
                    <th className="py-2 font-semibold">Descripción</th>
                  </tr>
                </thead>
                <tbody>
                  {carga.packages.map((pieza) => (
                    <tr key={pieza.id} className="border-t">
                      <td className="py-2">{ETIQUETA_PIEZA[pieza.package_type] ?? pieza.package_type}</td>
                      <td className="py-2">{pieza.quantity}</td>
                      <td className="py-2 text-[var(--texto-secundario)]">
                        {pieza.description ?? "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}

          {tieneDatosComerciales ? (
            <>
              <h3 className="mt-7 border-b pb-3 text-sm font-bold uppercase tracking-wide text-[var(--texto-secundario)]">
                Datos comerciales
              </h3>
              <dl className="grid grid-cols-2 divide-x border-b">
                <div className="pr-5">
                  <Dato etiqueta="Proveedor" valor={carga.shipper} />
                  {!ocultarDatosLogisticos ? (
                    <>
                      <Dato etiqueta="Carrier" valor={carga.carrier} />
                      <Dato etiqueta="Contenedor" valor={carga.container} />
                    </>
                  ) : null}
                </div>
                <div className="pl-5">
                  {!ocultarDatosLogisticos ? (
                    <>
                      <Dato etiqueta="Tracking" valor={carga.tracking} />
                      <Dato etiqueta="Orden de compra" valor={carga.po} />
                    </>
                  ) : null}
                  <Dato etiqueta="Número AMVAR" valor={carga.amvar} />
                  <Dato
                    etiqueta="Peso y volumen"
                    valor={medidas.length > 0 ? medidas.join(" · ") : null}
                  />
                </div>
              </dl>
            </>
          ) : null}
        </div>

        <div>
          <h2 className="border-b pb-3 text-base font-bold">Hitos logísticos</h2>
          <dl className="grid grid-cols-2 gap-x-6 border-b">
            <Dato etiqueta="Recibida" valor={formatearFechaHora(carga.received_at)} />
            <Dato etiqueta="Almacenada" valor={formatearFechaHora(carga.stored_at)} />
            <Dato etiqueta="Despachada" valor={formatearFechaHora(carga.dispatched_at)} />
            <Dato etiqueta="Entregada" valor={formatearFechaHora(carga.delivered_at)} />
          </dl>
          <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-xs text-[var(--texto-secundario)]">
            <span>Creada {formatearFechaHora(carga.created_at)}</span>
            <span>Actualizada {formatearFechaHora(carga.updated_at)}</span>
            <span>Versión {carga.row_version}</span>
          </div>
          {esTransito ? (
            <section className="mt-6" aria-labelledby="datos-aduaneros">
              <h3 id="datos-aduaneros" className="border-b pb-3 text-base font-bold">
                Descripción y partida arancelaria
              </h3>
              {esCliente ? (
                <FormularioDatosTransito
                  cargaId={carga.id}
                  rowVersion={carga.row_version}
                  descripcionInicial={carga.description}
                  partidaInicial={carga.tariff_code ?? null}
                />
              ) : (
                <dl className="grid grid-cols-2 gap-x-6 border-b">
                  <Dato etiqueta="Descripción en español" valor={carga.description} />
                  <Dato etiqueta="Partida arancelaria" valor={carga.tariff_code} />
                </dl>
              )}
            </section>
          ) : null}
        </div>
          </section>
        </>
      ) : null}

      {vista === "historial" ? (
        <section aria-labelledby="historial-carga">
          <h2 id="historial-carga" className="mb-6 text-base font-bold">
            Línea de tiempo
          </h2>
          <TimelineCarga cargaId={carga.id} />
        </section>
      ) : null}
    </div>
  );
}
