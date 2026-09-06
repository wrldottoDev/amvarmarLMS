import { expect, test } from "@playwright/test";
import { iniciarSesion } from "./ayudas";

// El backend de este spec corre con `COPILOT_PROVEEDOR_FALSO=true` (ver
// `playwright.config.ts`, webServer del backend) — el turno completo
// (frontend → SSE → backend → ejecutor de lectura → base real) se prueba sin
// tocar la API real de OpenAI. Si corrés este spec contra un backend YA
// levantado a mano (`reuseExistingServer` lo reusa tal cual), ESE proceso
// también necesita la variable, o el test termina llamando al proveedor real.
test("abre AMVI, pregunta por una carga y responde con el proveedor falso", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chromium");

  await iniciarSesion(page, "operaciones@demo.amvarmar.com");

  // La carga con WR fijo ("DEMO-WR-0001") es la única referencia estable
  // entre corridas — el número SHP se asigna en el seed y no es predecible.
  await page.goto("/shipments");
  await page.getByText("Filtros", { exact: true }).click();
  await page.getByLabel("Número WR").fill("DEMO-WR-0001");
  await page.getByRole("button", { name: "Aplicar" }).click();
  await page.getByRole("link", { name: "DEMO-WR-0001", exact: true }).click();

  const numeroCarga = (await page.locator("p.font-mono").innerText()).trim();
  expect(numeroCarga).toMatch(/^SHP-\d{4}-\d+$/);

  await page.getByRole("button", { name: /^Abrir AMVI/ }).click();
  const dialogo = page.getByRole("dialog", { name: "AMVI" });
  await expect(dialogo).toBeVisible();

  const campo = dialogo.getByLabel("Mensaje para el asistente");
  await campo.fill(`¿Cómo está la carga ${numeroCarga}?`);
  await campo.press("Enter");

  // El proveedor falso siempre responde con el código de carga en el texto
  // (`provider_falso.py`), así que el frontend lo convierte en enlace
  // (`fragmentarConEnlaces`) — confirma el turno completo, no solo que llegó
  // texto.
  const enlaceRespuesta = dialogo.getByRole("link", { name: numeroCarga });
  await expect(enlaceRespuesta).toBeVisible({ timeout: 15_000 });
  await expect(enlaceRespuesta).toHaveAttribute("href", `/shipments?q=${numeroCarga}`);

  await enlaceRespuesta.click();
  await expect(page).toHaveURL(new RegExp(`/shipments\\?q=${numeroCarga}`));
  await expect(page.getByRole("link", { name: "DEMO-WR-0001", exact: true })).toBeVisible();
});

test("una pregunta sin código de carga responde el texto fijo del proveedor falso", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chromium");

  await iniciarSesion(page, "cliente@demo.amvarmar.com");

  await page.getByRole("button", { name: /^Abrir AMVI/ }).click();
  const dialogo = page.getByRole("dialog", { name: "AMVI" });
  const campo = dialogo.getByLabel("Mensaje para el asistente");
  await campo.fill("hola, ¿cómo estás?");
  await campo.press("Enter");

  await expect(dialogo.getByText(/proveedor falso/i)).toBeVisible({ timeout: 15_000 });

  // Escape cierra el panel (requisito de accesibilidad de la Fase 3).
  await page.keyboard.press("Escape");
  await expect(dialogo).toBeHidden();
});
