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

El access token vive solo en memoria. `src/lib/api/refresh-mutex.ts` coordina solicitudes y pestañas
para que una ráfaga de respuestas `401` produzca una única rotación del refresh token.
