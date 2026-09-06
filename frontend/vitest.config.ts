import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/**
 * Configuración de las pruebas del frontend.
 *
 * Existe por el alias `@/`: sin esta resolución, cualquier prueba de un módulo
 * que importe otro por ruta absoluta falla al cargar, y eso deja fuera de las
 * pruebas a casi todo `src/`.
 */
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
