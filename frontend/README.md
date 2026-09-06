# AMVARMAR LMS Frontend

Interfaz web del sistema de gestión logística de AMVARMAR. Construida con Next.js App Router,
TypeScript, TanStack Query y Zod.

## Desarrollo local

Con FastAPI ejecutándose en `http://127.0.0.1:8001`:

```bash
npm install
cp .env.example .env.local
npm run api:generate
npm run dev
```

Next sirve la aplicación en `http://127.0.0.1:3000` y redirige `/api/*` al destino definido en
`API_PROXY_TARGET`. Mantener API y frontend bajo el mismo origen permite que la cookie HttpOnly de
refresh conserve su ruta restringida.

## Contrato API

`src/lib/api/generated.ts` se genera desde el OpenAPI del backend y no se edita manualmente:

```bash
OPENAPI_URL=http://127.0.0.1:8001/openapi.json npm run api:generate
```

## Verificación

```bash
npm test
npm run types
npm run lint
npm run build
```

## Pruebas end-to-end (Playwright)

`playwright.config.ts` levanta backend y frontend solo, sin sembrar datos.
Los specs de `e2e/` esperan la base de datos de demostración (empresa
"Importaciones Alfa S.A.", WR "DEMO-WR-0001", despachos "DSP-...", las
cuentas `*@demo.amvarmar.com`), así que hay que sembrarla una vez antes de
correr las pruebas:

```bash
cd ../backend && ENVIRONMENT=local ./.venv/bin/python -m scripts.seed_demo
cd ../frontend && npm run test:e2e
```

`seed_demo` es idempotente: correrlo de nuevo no duplica nada.

`e2e/asistente.spec.ts` prueba el chat AMVI contra `ProveedorFalsoDeterministico`
(`app/modules/copilot/provider_falso.py`), no contra la API real de OpenAI —
`playwright.config.ts` ya arranca el backend con `COPILOT_PROVEEDOR_FALSO=true`.
Si en cambio corrés Playwright contra un backend que ya tenías levantado a
mano (`reuseExistingServer` lo reusa tal cual), ese proceso necesita la misma
variable (y `ENVIRONMENT=local`), o ese spec termina llamando al proveedor
real.

El access token vive solo en memoria. `src/lib/api/refresh-mutex.ts` coordina solicitudes y pestañas
para que una ráfaga de respuestas `401` produzca una única rotación del refresh token.
