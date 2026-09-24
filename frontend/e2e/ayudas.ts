import { expect, type Page } from "@playwright/test";

// Prerrequisito de todos los specs: datos de `seed_demo` ya sembrados.
// `playwright.config.ts` no siembra nada — solo levanta backend y frontend —
// así que hace falta correr una vez, antes de `npm run test:e2e`:
//
//   cd backend && ENVIRONMENT=local ./.venv/bin/python -m scripts.seed_demo
export const PASSWORD_DEMO = "Demo-AMVARMAR-2026";

export async function iniciarSesion(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Correo electrónico").fill(email);
  await page.locator('input[name="password"]').fill(PASSWORD_DEMO);
  await page.getByRole("button", { name: "Ingresar" }).click();
  await expect(page).toHaveURL(/\/(?:operaciones|dashboard)$/, { timeout: 15_000 });
}

export async function esperarSinDesbordeHorizontal(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(() => {
        const y = window.scrollY;
        window.scrollTo(10_000, y);
        const seDesplazo = window.scrollX !== 0;
        window.scrollTo(0, y);
        return !seDesplazo && document.body.scrollWidth <= window.innerWidth + 1;
      }),
    )
    .toBe(true);
}
