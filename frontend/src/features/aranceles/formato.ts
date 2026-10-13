/** "1730870.4" → "¢1.730.870,40", sin pasar por números de punto flotante. */
export function colones(valor: string): string {
  const [entero, decimales = ""] = valor.split(".");
  return `¢${entero.replace(/\B(?=(\d{3})+(?!\d))/g, ".")},${decimales.padEnd(2, "0").slice(0, 2)}`;
}

/** "14" → "14 %", "0.4760" → "0,476 %". Solo se quitan ceros después de la coma. */
export function porcentaje(valor: string): string {
  const limpio = valor.includes(".")
    ? valor.replace(/0+$/, "").replace(/\.$/, "")
    : valor;
  return `${limpio.replace(".", ",")} %`;
}
