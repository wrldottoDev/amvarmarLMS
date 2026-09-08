"use client";

import { ArrowLeft, Save } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useState } from "react";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { CargandoPagina } from "@/components/ui/estados-pagina";
import { useUbicaciones } from "@/features/admin/consultas";
import {
  aPayload,
  EditorPiezas,
  type Pieza,
  problemaDePiezas,
} from "@/components/shipments/editor-piezas";
import {
  EditorPeso,
  pesoParaApi,
  pesoValido,
  type PesoEditable,
} from "@/components/shipments/editor-peso";
import {
  useActualizarCarga,
  useCarga,
  useReemplazarPiezas,
} from "@/features/shipments/consultas";

/**
 * Corregir los datos de una carga.
 *
 * Equivale a `edit_warehouse.html` del sistema viejo. No cambia el estado —eso
 * va por transiciones, que dejan su evento— ni la empresa dueña: mover una
 * carga de cliente es un movimiento contable, no una corrección de tipeo.
 *
 * Se mandan solo los campos que la persona tocó. Enviar el objeto entero haría
 * que dos personas editando campos distintos se pisaran igual, que es
 * exactamente lo que `row_version` existe para evitar.
 */
export default function PaginaEditarCarga({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const carga = useCarga(id);
  const ubicaciones = useUbicaciones();
  const actualizar = useActualizarCarga(id);
  const reemplazarPiezas = useReemplazarPiezas(id);

  // `null` = sin tocar. Distinto de cadena vacía, que significa "borralo".
  const [cambios, setCambios] = useState<Record<string, string>>({});
  // `null` mientras no se toque el desglose: así guardar solo el shipper no
  // reescribe piezas que nadie miró.
  const [piezas, setPiezas] = useState<Pieza[] | null>(null);
  const [peso, setPeso] = useState<PesoEditable | null>(null);

  if (carga.isPending || ubicaciones.isPending) return <CargandoPagina />;
  if (carga.isError) return <AvisoError error={carga.error} />;

  const datos = carga.data;

  const piezasActuales: Pieza[] =
    piezas ??
    datos.packages.map((p) => ({
      clave: p.id,
      package_type: p.package_type,
      quantity: String(p.quantity),
      description: p.description ?? "",
      weight_kg: p.weight_kg == null ? "" : String(p.weight_kg),
      length_cm: p.length_cm == null ? "" : String(p.length_cm),
      width_cm: p.width_cm == null ? "" : String(p.width_cm),
      height_cm: p.height_cm == null ? "" : String(p.height_cm),
    }));

  const pesoActual: PesoEditable =
    peso ??
    ({
      kg: datos.weight_kg == null ? "" : String(datos.weight_kg),
      lb: datos.weight_lb == null ? "" : String(datos.weight_lb),
      unidadFuente: datos.weight_source_unit ?? "KG",
    } satisfies PesoEditable);

  function valor(campo: string, actual: string | number | null | undefined): string {
    return cambios[campo] ?? (actual == null ? "" : String(actual));
  }

  function cambiar(campo: string, nuevo: string) {
    setCambios((actuales) => ({ ...actuales, [campo]: nuevo }));
  }

  async function guardar() {
    const cuerpo: Record<string, unknown> = { row_version: datos.row_version };

    for (const [campo, texto] of Object.entries(cambios)) {
      const limpio = texto.trim();
      // Los numéricos van como número o `null`; el resto como texto. La cadena
      // vacía en un identificador significa borrarlo, y eso el backend lo
      // entiende, así que no se descarta.
      cuerpo[campo] = NUMERICOS.has(campo) ? (limpio === "" ? null : limpio) : limpio;
    }
    if (peso !== null) cuerpo.weight = pesoParaApi(peso);

    // Las piezas van por su propio endpoint atómico, y primero: si fallan, no
    // queda una carga con datos corregidos y desglose viejo.
    //
    // Cada llamada sube `row_version`, así que la segunda usa la que devolvió
    // la primera. Mandar la original daría un 409 contra un cambio propio.
    let version = datos.row_version;

    if (piezas !== null) {
      const resultado = await reemplazarPiezas.mutateAsync({
        row_version: version,
        packages: aPayload(piezas) as never,
      });
      version = resultado.row_version;
    }

    if (Object.keys(cambios).length > 0) {
      await actualizar.mutateAsync({ ...cuerpo, row_version: version } as never);
    }

    router.push(`/shipments/${id}`);
  }

  const problema = piezas === null ? null : problemaDePiezas(piezas);
  const sinCambios = Object.keys(cambios).length === 0 && piezas === null && peso === null;

  return (
    <section className="mx-auto max-w-4xl space-y-4">
      <Link
        href={`/shipments/${id}`}
        className="inline-flex items-center gap-1.5 text-sm text-[var(--texto-secundario)] hover:underline"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        Volver a la carga
      </Link>

      <div>
        <h1 className="text-2xl font-bold text-[var(--mar)]">Editar carga</h1>
        <p className="mt-1 text-sm text-[var(--texto-secundario)]">
          {datos.wr || datos.invoice || datos.shipment_number} ·{" "}
          <span className="font-mono">{datos.shipment_number}</span>
        </p>
      </div>

      <fieldset className="space-y-3 rounded-lg border bg-[var(--superficie)] p-4">
        <legend className="px-1 font-semibold">Identificadores</legend>
        <p className="text-xs text-[var(--texto-secundario)]">
          Vaciar uno lo borra. Es la forma de corregir un número mal tecleado.
        </p>

        <div className="grid gap-3 sm:grid-cols-2">
          {(
            [
              ["wr", "Warehouse Receipt", datos.wr],
              ["invoice", "Factura", datos.invoice],
              ["tracking", "Tracking", datos.tracking],
              ["po", "Orden de compra (PO)", datos.po],
              ["container", "Contenedor", datos.container],
            ] as const
          ).map(([campo, etiqueta, actual]) => (
            <label className="block" key={campo}>
              <span className="mb-1 block text-sm font-medium">{etiqueta}</span>
              <input
                className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
                value={valor(campo, actual)}
                onChange={(evento) => cambiar(campo, evento.target.value)}
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
              value={valor("transport_mode", datos.transport_mode)}
              onChange={(evento) => cambiar("transport_mode", evento.target.value)}
            >
              <option value="">Sin registrar</option>
              <option value="SEA">Marítimo</option>
              <option value="AIR">Aéreo</option>
              <option value="LAND">Terrestre</option>
            </select>
          </label>
          {(
            [
              ["shipper", "Shipper", datos.shipper],
              ["carrier", "Carrier", datos.carrier],
            ] as const
          ).map(([campo, etiqueta, actual]) => (
            <label className="block" key={campo}>
              <span className="mb-1 block text-sm font-medium">{etiqueta}</span>
              <input
                className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
                value={valor(campo, actual)}
                onChange={(evento) => cambiar(campo, evento.target.value)}
              />
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset className="space-y-3 rounded-lg border bg-[var(--superficie)] p-4">
        <legend className="px-1 font-semibold">Peso y volumen</legend>
        <EditorPeso
          valor={pesoActual}
          alCambiar={setPeso}
          mostrarError={peso !== null && !pesoValido(peso)}
        />
        <div className="grid gap-3 sm:grid-cols-3">
          {(
            [
              ["volumetric_weight_kg", "Volumétrico (kg)", datos.volumetric_weight_kg],
              ["volume_m3", "Volumen (m³)", datos.volume_m3],
              ["foots_cft", "Pies cúbicos (CFT)", datos.foots_cft],
            ] as const
          ).map(([campo, etiqueta, actual]) => (
            <label className="block" key={campo}>
              <span className="mb-1 block text-sm font-medium">{etiqueta}</span>
              <input
                type="number"
                min="0"
                step="0.001"
                className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
                value={valor(campo, actual)}
                onChange={(evento) => cambiar(campo, evento.target.value)}
              />
            </label>
          ))}
        </div>
      </fieldset>

      <EditorPiezas piezas={piezasActuales} alCambiar={setPiezas} />

      <fieldset className="space-y-3 rounded-lg border bg-[var(--superficie)] p-4">
        <legend className="px-1 font-semibold">Ruta y entrega</legend>

        <label className="block">
          <span className="mb-1 block text-sm font-medium">Llega a</span>
          <select
            className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
            value={valor("destination_location_id", datos.destination.id)}
            onChange={(evento) => cambiar("destination_location_id", evento.target.value)}
          >
            {ubicaciones.data?.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name} ({u.location_code})
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-medium">Dirección de entrega</span>
          <input
            className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
            value={valor("destination_address", datos.destination_address)}
            onChange={(evento) => cambiar("destination_address", evento.target.value)}
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-medium">¿Qué viene?</span>
          <textarea
            rows={2}
            className="w-full rounded-md border bg-[var(--superficie)] px-3 py-2 text-sm"
            value={valor("description", datos.description)}
            onChange={(evento) => cambiar("description", evento.target.value)}
          />
        </label>
      </fieldset>

      {actualizar.error ? <AvisoError error={actualizar.error} /> : null}
      {reemplazarPiezas.error ? <AvisoError error={reemplazarPiezas.error} /> : null}

      <div className="flex justify-end gap-2 pb-4">
        <Link
          href={`/shipments/${id}`}
          className="flex h-10 items-center rounded-md border px-4 text-sm font-medium hover:bg-[var(--hover)]"
        >
          Cancelar
        </Link>
        <Boton
          onClick={() => void guardar()}
          disabled={sinCambios || problema !== null || (peso !== null && !pesoValido(peso))}
          cargando={actualizar.isPending || reemplazarPiezas.isPending}
        >
          <Save className="size-4" aria-hidden="true" />
          Guardar cambios
        </Boton>
      </div>
    </section>
  );
}

/** Campos que el backend espera como número, no como texto. */
const NUMERICOS = new Set([
  "volumetric_weight_kg",
  "volume_m3",
  "foots_cft",
]);
