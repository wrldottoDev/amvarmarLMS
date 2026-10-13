"use client";

import { useMutation } from "@tanstack/react-query";
import { Calculator, FileText } from "lucide-react";
import { type FormEvent, useState } from "react";
import { AvisoError } from "@/components/ui/aviso-error";
import { Boton } from "@/components/ui/boton";
import { Campo } from "@/components/ui/campo";
import { colones, porcentaje } from "@/features/aranceles/formato";
import { api, exigirDatos } from "@/lib/api/client";

/**
 * Calculadora de impuestos de prueba (estimador, fase F1), solo para AMVARMAR.
 *
 * Las tasas se ponen a mano, copiadas del DUA o de TICA: todavía no hay
 * catálogo arancelario. Sirve para contrastar el motor con DUAs reales antes
 * de construir la calculadora pública (docs/estimador-impuestos/).
 */

type Datos = Record<
  | "valor_mercancia_usd"
  | "flete_usd"
  | "seguro_usd"
  | "tipo_cambio"
  | "dai"
  | "ley_6946"
  | "selectivo"
  | "margen_ge"
  | "iva"
  | "otros_cargos_crc",
  string
>;

const VACIO: Datos = {
  valor_mercancia_usd: "",
  flete_usd: "0",
  seguro_usd: "0",
  tipo_cambio: "",
  dai: "",
  ley_6946: "1",
  selectivo: "0",
  margen_ge: "0",
  iva: "13",
  otros_cargos_crc: "0",
};

// Instructivo DGA MH-DGA-PRO09-INS-004, p. 18: el total tiene que dar ¢1.730.870,40.
const EJEMPLO_DGA: Datos = {
  ...VACIO,
  valor_mercancia_usd: "9600",
  tipo_cambio: "602",
  dai: "14",
};

const CAMPOS: { clave: keyof Datos; etiqueta: string; ayuda?: string }[] = [
  {
    clave: "valor_mercancia_usd",
    etiqueta: "Valor de la mercancía (US$)",
    ayuda: "Precio pagado (FOB).",
  },
  {
    clave: "flete_usd",
    etiqueta: "Flete (US$)",
    ayuda: "Hasta el primer puerto centroamericano.",
  },
  { clave: "seguro_usd", etiqueta: "Seguro (US$)" },
  {
    clave: "tipo_cambio",
    etiqueta: "Tipo de cambio (¢ por US$)",
    ayuda: "El del DUA: venta del BCCR del día de aceptación.",
  },
  { clave: "dai", etiqueta: "DAI (%)" },
  { clave: "ley_6946", etiqueta: "Ley 6946 (%)" },
  { clave: "selectivo", etiqueta: "Selectivo de Consumo (%)" },
  {
    clave: "margen_ge",
    etiqueta: "Margen G/E (%)",
    ayuda: "Solo agranda la base del IVA.",
  },
  { clave: "iva", etiqueta: "IVA (%)" },
  {
    clave: "otros_cargos_crc",
    etiqueta: "Otros cargos (¢)",
    ayuda: "Timbres, Caldera: ya en colones.",
  },
];

export default function PaginaCalculadoraImpuestos() {
  const [datos, setDatos] = useState<Datos>(VACIO);
  const [scIncluye6946, setScIncluye6946] = useState(false);

  const estimar = useMutation({
    mutationFn: async () => {
      // La coma decimal de Costa Rica se acepta y se manda con punto.
      const cuerpo = Object.fromEntries(
        Object.entries(datos).map(([clave, valor]) => [
          clave,
          valor.trim().replace(",", ".") || "0",
        ]),
      ) as Datos;
      return exigirDatos(
        await api.POST("/api/v1/aranceles/estimar", {
          body: { ...cuerpo, sc_incluye_ley_6946: scIncluye6946 },
        }),
      );
    },
  });

  function enviar(evento: FormEvent) {
    evento.preventDefault();
    estimar.mutate();
  }

  const resultado = estimar.data;

  return (
    <section className="mx-auto max-w-4xl space-y-6">
      <header>
        <p className="text-xs font-bold uppercase tracking-wide text-[var(--marca)]">
          Prueba interna
        </p>
        <h1 className="mt-1 text-2xl font-bold">Calculadora de impuestos</h1>
        <p className="mt-1 text-sm text-[var(--texto-secundario)]">
          Copiá las tasas del DUA o de TICA, sin separador de miles (decimales
          con coma o punto). Calcula en cascada como el instructivo de la DGA.
          Es una estimación de prueba, no una liquidación oficial.
        </p>
      </header>

      <form
        onSubmit={enviar}
        className="space-y-4 rounded-lg border bg-[var(--superficie)] p-4"
      >
        <div className="grid gap-3 sm:grid-cols-2">
          {CAMPOS.map(({ clave, etiqueta, ayuda }) => (
            <Campo
              key={clave}
              id={clave}
              etiqueta={etiqueta}
              ayuda={ayuda}
              inputMode="decimal"
              autoComplete="off"
              value={datos[clave]}
              onChange={(evento) =>
                setDatos((actuales) => ({
                  ...actuales,
                  [clave]: evento.target.value,
                }))
              }
            />
          ))}
        </div>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1"
            checked={scIncluye6946}
            onChange={(evento) => setScIncluye6946(evento.target.checked)}
          />
          <span>
            Sumar la Ley 6946 a la base del Selectivo
            <span className="block text-xs text-[var(--texto-secundario)]">
              Duda abierta: la fórmula general no la suma y el ejemplo del
              vehículo del instructivo sí. Si el total no coincide con el DUA,
              probá marcándola.
            </span>
          </span>
        </label>
        <div className="flex flex-wrap gap-2">
          <Boton type="submit" disabled={estimar.isPending}>
            <Calculator className="size-4" aria-hidden="true" />
            {estimar.isPending ? "Calculando…" : "Calcular"}
          </Boton>
          <Boton
            type="button"
            variante="secundario"
            onClick={() => {
              setDatos(EJEMPLO_DGA);
              setScIncluye6946(false);
            }}
          >
            <FileText className="size-4" aria-hidden="true" />
            Cargar ejemplo oficial (DGA p. 18)
          </Boton>
        </div>
      </form>

      {estimar.isError ? <AvisoError error={estimar.error} /> : null}

      {resultado ? (
        <div className="space-y-3 rounded-lg border bg-[var(--superficie)] p-4">
          <p className="text-sm text-[var(--texto-secundario)]">
            Valor aduanero {colones(resultado.valor_aduanero_usd).replace("¢", "US$")} ={" "}
            {colones(resultado.valor_aduanero_crc)}
          </p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-sm">
              <thead className="text-left text-xs uppercase text-[var(--texto-secundario)]">
                <tr>
                  <th className="py-2 font-semibold">Concepto</th>
                  <th className="py-2 text-right font-semibold">Base</th>
                  <th className="py-2 text-right font-semibold">Tasa</th>
                  <th className="py-2 text-right font-semibold">Monto</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {resultado.lineas.map((linea) => (
                  <tr key={linea.concepto}>
                    <td className="py-2">
                      {linea.concepto}
                      {linea.suma_al_total ? null : (
                        <span className="block text-xs text-[var(--texto-secundario)]">
                          No suma al total: solo agranda la base del IVA
                        </span>
                      )}
                    </td>
                    <td className="py-2 text-right tabular-nums">
                      {colones(linea.base)}
                    </td>
                    <td className="py-2 text-right tabular-nums">
                      {linea.tasa === null ? "" : porcentaje(linea.tasa)}
                    </td>
                    <td className="py-2 text-right font-medium tabular-nums">
                      {colones(linea.monto)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t-2">
                  <td className="py-3 font-bold" colSpan={3}>
                    Total de impuestos
                  </td>
                  <td className="py-3 text-right text-lg font-bold tabular-nums text-[var(--mar)]">
                    {colones(resultado.total)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
          <p className="text-xs text-[var(--texto-secundario)]">
            Reglas {resultado.reglas_version}. Base del Selectivo{" "}
            {resultado.sc_incluye_ley_6946 ? "con" : "sin"} Ley 6946; cada monto
            redondeado a céntimos. No incluye preferencias por tratado, INDER,
            IFAM ni impuestos específicos. Honorarios del agente, almacenaje y
            fletes locales no son impuestos y no están acá.
          </p>
        </div>
      ) : null}
    </section>
  );
}
