import type { NextConfig } from "next";

const nextConfig: NextConfig = {
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
