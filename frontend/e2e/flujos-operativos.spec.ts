import { expect, test } from "@playwright/test";
import { esperarSinDesbordeHorizontal, iniciarSesion } from "./ayudas";

// Prerrequisito: estos specs asumen datos de `seed_demo` ya sembrados
// (empresa "Importaciones Alfa S.A.", el WR "DEMO-WR-0001", despachos
// "DSP-...", las cuentas de `ayudas.ts`). `playwright.config.ts` no siembra
// nada — solo levanta backend y frontend — así que hace falta correr una vez,
// antes de `npm run test:e2e`:
//
//   cd backend && ENVIRONMENT=local ./.venv/bin/python -m scripts.seed_demo
//
// Se evaluó automatizarlo con `globalSetup`, pero eso acopla el test runner
// de Node a invocar el venv de Python con las variables de entorno correctas
// solo para un caso de uso local (CI no corre Playwright, ver ci.yml) — más
// costoso que documentarlo acá y en frontend/README para lo que resuelve.

test("operaciones busca cargas y revisa un despacho completo", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chromium");

  await iniciarSesion(page, "operaciones@demo.amvarmar.com");
  await page.goto("/shipments");
  await expect(page.getByRole("heading", { name: "Cargas" })).toBeVisible();

  await page.getByText("Filtros", { exact: true }).click();
  await page.getByLabel("Número WR").fill("DEMO-WR-0001");
  await page.getByRole("button", { name: "Aplicar" }).click();

  await expect(page).toHaveURL(/(?:\?|&)wr=DEMO-WR-0001(?:&|$)/);
  await expect(page.getByLabel("Filtros activos")).toContainText("WR: DEMO-WR-0001");
  await expect(page.getByRole("link", { name: "DEMO-WR-0001", exact: true })).toBeVisible();
  await esperarSinDesbordeHorizontal(page);
  await page.screenshot({ path: testInfo.outputPath("cargas-filtradas-desktop.png"), fullPage: true });

  await page.goto("/despachos");
  const enlaceDespacho = page.locator('a[href^="/despachos/"]').filter({ hasText: /DSP-/ }).first();
  await expect(enlaceDespacho).toBeVisible();
  await enlaceDespacho.click();

  await expect(page.getByRole("heading", { name: "Cargas incluidas" })).toBeVisible();
  await expect(page.locator('section[aria-labelledby="cargas-incluidas"] a[href^="/shipments/"]')).toHaveCount(2);
  await expect(page.getByRole("button", { name: "Registrar salida" })).toBeVisible();
  await esperarSinDesbordeHorizontal(page);
  await page.screenshot({ path: testInfo.outputPath("despacho-desktop.png"), fullPage: true });
});

test("CLIENT_USER puede preparar cargas y despachos desde móvil", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-chromium");

  await iniciarSesion(page, "cliente2@demo.amvarmar.com");
  await page.getByRole("button", { name: "Mostrar u ocultar el menú" }).click();
  await page.locator('aside[aria-label="Navegación principal"]:visible a[href="/shipments"]').click();
  await expect(page.getByRole("link", { name: "Nueva carga" })).toBeVisible();
  await page.getByRole("link", { name: "Nueva carga" }).click();

  await page.getByRole("button", { name: /De otro origen/ }).click();
  await expect(page.getByText("Importaciones Alfa S.A.", { exact: true })).toBeVisible();
  await expect(page.getByText(/La carga se registra como prealerta/)).toBeVisible();
  await expect(page.getByLabel("Estado en que se registra")).toHaveCount(0);

  const origen = page.getByLabel("Sale de");
  const destino = page.getByLabel("Llega a");
  const ubicaciones = await origen.locator('option:not([value=""])').evaluateAll((opciones) =>
    opciones.map((opcion) => (opcion as HTMLOptionElement).value),
  );
  expect(ubicaciones.length).toBeGreaterThanOrEqual(2);
  await origen.selectOption(ubicaciones[0]);
  await destino.selectOption(ubicaciones[1]);
  await page.getByLabel(/^Factura/).fill("E2E-VISUAL-001");

  await page.getByLabel("Peso en kg").fill("100");
  await page.getByLabel("Peso en kg").blur();
  await expect(page.getByLabel("Peso en libras")).toHaveValue("220.462");
  await page.getByLabel("Cantidad").fill("3");
  await expect(page.getByText("3 unidades", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Quitar la pieza 1" })).toBeDisabled();
  await page.getByLabel("Método de transporte").selectOption("LAND");
  await expect(page.getByLabel("Método de transporte")).toHaveValue("LAND");
  await expect(page.getByRole("button", { name: "Crear carga" })).toBeEnabled();

  await page.getByText("Peso y volumen").scrollIntoViewIfNeeded();
  await esperarSinDesbordeHorizontal(page);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: testInfo.outputPath("nueva-carga-mobile.png"), fullPage: true });

  await page.goto("/despachos");
  await expect(page.getByRole("link", { name: "Solicitar despacho" })).toBeVisible();
  const enlaceDespacho = page.locator('a[href^="/despachos/"]').filter({ hasText: /DSP-/ }).first();
  await expect(enlaceDespacho).toBeVisible();
  await enlaceDespacho.click();
  await expect(page.getByRole("heading", { name: "Cargas incluidas" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Registrar salida" })).toHaveCount(0);
  await esperarSinDesbordeHorizontal(page);
  await page.screenshot({ path: testInfo.outputPath("despacho-client-user-mobile.png"), fullPage: true });
});

test("CLIENT_ADMIN crea una carga y la ve en el listado", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chromium");

  // Referencia única por corrida: una factura fija colisionaría con
  // `uq_shipment_references_valor` en la segunda vuelta del test.
  const factura = `E2E-WRITE-${Date.now()}`;

  await iniciarSesion(page, "cliente@demo.amvarmar.com");
  await page.goto("/cargas/nueva");
  await expect(page.getByRole("heading", { name: "Nueva carga" })).toBeVisible();

  // "De otro origen" evita depender de que exista una bodega con WR
  // configurada — la carga se identifica por su factura, que ya se llena
  // igual.
  await page.getByRole("button", { name: /De otro origen/ }).click();

  const origen = page.getByLabel("Sale de");
  const destino = page.getByLabel("Llega a");
  const ubicaciones = await origen.locator('option:not([value=""])').evaluateAll((opciones) =>
    opciones.map((opcion) => (opcion as HTMLOptionElement).value),
  );
  expect(ubicaciones.length).toBeGreaterThanOrEqual(2);
  await origen.selectOption(ubicaciones[0]);
  await destino.selectOption(ubicaciones[1]);

  await page.getByLabel(/^Factura/).fill(factura);
  await page.getByLabel("Peso en kg").fill("50");
  await page.getByLabel("Peso en kg").blur();

  // La pieza inicial ya trae cantidad 1: "toda carga debe conservar al menos
  // una pieza" (04-reglas-negocio-objetivo.md) se cumple por defecto y no
  // hace falta tocar el editor de piezas para que el botón se habilite.
  const botonCrear = page.getByRole("button", { name: "Crear carga" });
  await expect(botonCrear).toBeEnabled();
  await botonCrear.click();

  // El alta redirige al expediente, no al listado (ver comentario en
  // cargas/nueva/page.tsx): ahí es donde se confirma el número asignado.
  await expect(page).toHaveURL(/\/cargas\/[0-9a-f-]+\/archivos$/, { timeout: 15_000 });
  await expect(page.getByText("Carga creada")).toBeVisible();
  const numeroAsignado = await page.locator("span.font-mono").first().innerText();
  expect(numeroAsignado).toMatch(/^SHP-\d{4}-\d+$/);

  // La lista identifica la fila por WR o factura, no por el número interno
  // (ver `referencia()` en listado-cargas.tsx) — esta carga no tiene WR, así
  // que se ve por su factura. Buscar por `numeroAsignado` en vez de por la
  // factura confirma además que el número devuelto es el mismo que indexó la
  // búsqueda global (`q` busca "Numero SHP", 04-reglas-negocio-objetivo.md).
  await page.goto("/shipments");
  await page.getByLabel("Buscar cargas").fill(numeroAsignado);
  await page.getByLabel("Buscar cargas").press("Enter");

  await expect(page.getByRole("link", { name: `Ver carga ${factura}`, exact: true })).toBeVisible();
});
