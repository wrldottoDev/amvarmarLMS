import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Imagen de Docker mínima (`infra/produccion`): `.next/standalone` trae su
  // propio `server.js` y solo las dependencias que usa. En producción `/api` lo
  // resuelve nginx contra el backend, así que el rewrite de abajo solo sirve en
  // desarrollo y la imagen no depende de dónde corre la API.
  output: "standalone",
  turbopack: {
    root: process.cwd(),
  },
  async rewrites() {
    const destino = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8001";

    return [
      {
        source: "/api/:path*",
        destination: `${destino}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
