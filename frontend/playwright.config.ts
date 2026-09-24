import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: [["line"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: [
    {
      command:
        "DEBUG=false ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001",
      cwd: "../backend",
      url: "http://127.0.0.1:8001/health/ready",
      reuseExistingServer: true,
      timeout: 120_000,
      // ADR-0012 (Fase 3): AMVI corre contra `ProveedorFalsoDeterministico`
      // acá, nunca contra la API real de OpenAI — ver
      // `app/modules/copilot/provider_falso.py`. `Settings` rechaza esta
      // combinación fuera de `environment=local` (`app/core/config.py`), así
      // que no hace falta cuidar que se filtre a otro entorno.
      // Si corrés `npm run test:e2e` contra un backend YA levantado a mano
      // (`reuseExistingServer` lo reusa tal cual), ESE proceso necesita las
      // mismas dos variables, o `e2e/asistente.spec.ts` termina llamando a
      // la API real.
      env: {
        ENVIRONMENT: "local",
        COPILOT_PROVEEDOR_FALSO: "true",
      },
    },
    {
      command: "npm run dev -- --hostname 127.0.0.1 --port 3000",
      cwd: ".",
      url: "http://127.0.0.1:3000/login",
      reuseExistingServer: true,
      timeout: 120_000,
    },
  ],
  projects: [
    {
      name: "desktop-chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } },
    },
    {
      name: "mobile-chromium",
      use: { ...devices["Pixel 7"] },
    },
  ],
});
