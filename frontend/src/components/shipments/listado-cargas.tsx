"use client";

import { ArrowRight, EyeOff } from "lucide-react";
import Link from "next/link";
import { BadgeEstado, BadgePendientes } from "./badges-carga";
import { TransicionCarga } from "./transicion-carga";
import {
  columnasNumericas,
  contenidoColumna,
  usePreferenciaColumnas,
} from "@/features/shipments/columnas";
import type { CargaResumen } from "@/lib/api/tipos";
import { clases, formatearFecha } from "@/lib/utilidades";

/**
 * Cómo se identifica una carga en pantalla.
 *
 * Lo de Miami lleva Warehouse Receipt; lo demás va por factura. Ese es el orden
 * en que la gente la busca, así que ese es el orden en que se muestra. El
 * número interno solo aparece si no hay ninguno de los dos.
 */
function referencia(carga: CargaResumen) {
  return carga.wr || carga.invoice || carga.shipment_number;
}

function cantidadPendiente(carga: CargaResumen, esCliente: boolean) {
  return esCliente ? carga.client_action_required_count : carga.open_requirements_count;
}

export function ListadoCargas({
  cargas,
  esCliente,
  empresaVisible = false,
  soloLectura = false,
}: {
  cargas: CargaResumen[];
  esCliente: boolean;
  empresaVisible?: boolean;
  /** Historial de despachos (ADR-0007): una carga archivada no admite cambios
   * de estado, así que el badge de estado no abre el modal de transición. */
  soloLectura?: boolean;
}) {
  const { data: preferencia } = usePreferenciaColumnas();

  // Mientras carga la preferencia se usan las columnas mínimas, para que la
  // tabla no salte de ancho al llegar la respuesta.
  const visibles = preferencia?.visibles ?? ["identificador", "estado", "pendientes"];
  const etiquetas = new Map(preferencia?.disponibles.map((c) => [c.clave, c.etiqueta]) ?? []);

  // Un cliente ve una sola empresa: la columna sería la misma en cada fila.
  const columnas = visibles.filter((c) => c !== "empresa" || (empresaVisible && !esCliente));

  return (
    <>
      <div className="hidden overflow-hidden rounded-lg border bg-[var(--superficie)] md:block">
        <div className="relative overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead className="border-b bg-[var(--hover)] text-xs font-bold uppercase text-[var(--texto-secundario)]">
              <tr>
                {columnas.map((clave) => (
                  <th
                    key={clave}
                    className={clases(
                      "whitespace-nowrap px-4 py-3",
                      columnasNumericas.has(clave) && "text-right",
                    )}
                  >
                    {etiquetas.get(clave) ?? clave}
                  </th>
                ))}
                <th className="w-12 px-3 py-3">
                  <span className="sr-only">Ver</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {cargas.map((carga) => (
                <tr
                  key={carga.id}
                  className={clases("group hover:bg-[var(--hover)]", carga.hidden_at && "opacity-55")}
                >
                  {columnas.map((clave) => (
                    <td
                      key={clave}
                      className={clases(
                        "px-4 py-3.5 text-sm",
                        columnasNumericas.has(clave) && "text-right tabular-nums",
                      )}
                    >
                      <Celda clave={clave} carga={carga} esCliente={esCliente} soloLectura={soloLectura} />
                    </td>
                  ))}
                  <td className="px-3 py-3.5">
                    <Link
                      href={`/shipments/${carga.id}`}
                      className="grid size-9 place-items-center rounded-md text-[var(--mar)] opacity-60 hover:bg-[var(--marca-tenue)] group-hover:opacity-100"
                      title="Ver carga"
                      aria-label={`Ver carga ${referencia(carga)}`}
                    >
                      <ArrowRight className="size-4" />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* En móvil una tabla de trece columnas es inusable: se muestra lo que
          identifica la carga y su estado, que es con lo que se decide si abrirla. */}
      <ul className="grid gap-2 md:hidden">
        {cargas.map((carga) => (
          <li key={carga.id}>
            <div
              className={clases(
                "flex items-center gap-3 rounded-lg border bg-[var(--superficie)] px-4 py-3.5",
                carga.hidden_at && "opacity-55",
              )}
            >
              <Link href={`/shipments/${carga.id}`} className="min-w-0 flex-1">
                <strong className="block text-sm">{referencia(carga)}</strong>
                <span className="block text-xs text-[var(--texto-secundario)]">
                  {carga.origin.location_code} → {carga.destination.location_code} ·{" "}
                  {formatearFecha(carga.created_at)}
                </span>
              </Link>
              {soloLectura ? (
                <BadgeEstado estado={carga.status} />
              ) : (
                <TransicionCarga
                  cargaId={carga.id}
                  estado={carga.status}
                  rowVersion={carga.row_version}
                  modo="badge"
                />
              )}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

function Celda({
  clave,
  carga,
  esCliente,
  soloLectura,
}: {
  clave: string;
  carga: CargaResumen;
  esCliente: boolean;
  soloLectura: boolean;
}) {
  if (clave === "identificador") {
    return (
      <span className="flex items-center gap-1.5">
        {carga.hidden_at ? (
          <EyeOff
            className="size-3.5 shrink-0 text-[var(--texto-secundario)]"
            aria-label="Oculta"
          />
        ) : null}
        <Link
          href={`/shipments/${carga.id}`}
          className="font-semibold text-[var(--mar)] hover:underline"
        >
          {referencia(carga)}
        </Link>
      </span>
    );
  }

  if (clave === "estado") {
    return soloLectura ? (
      <BadgeEstado estado={carga.status} />
    ) : (
      <TransicionCarga
        cargaId={carga.id}
        estado={carga.status}
        rowVersion={carga.row_version}
        modo="badge"
      />
    );
  }
  if (clave === "pendientes")
    return <BadgePendientes cantidad={cantidadPendiente(carga, esCliente)} />;

  const valor = contenidoColumna[clave]?.(carga) ?? "—";
  return <span className={valor === "—" ? "text-[var(--texto-secundario)]" : ""}>{valor}</span>;
}
