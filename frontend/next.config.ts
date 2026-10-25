import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  turbopack: {
    root: __dirname,
  },
  images: {
    // The brand logo (public/logo.svg) is served through next/image, but the
    // image optimizer refuses SVGs by default (returns 400 → logo invisible).
    // It's a trusted, self-hosted asset, so allow it and sandbox it via CSP.
    dangerouslyAllowSVG: true,
    contentDispositionType: "attachment",
    contentSecurityPolicy: "default-src 'self'; script-src 'none'; sandbox;",
    remotePatterns: [
      {
        protocol: "http",
        hostname: "localhost",
        port: "8000",
        pathname: "/static/**",
      },
      {
        protocol: "http",
        hostname: "backend",
        port: "8000",
        pathname: "/static/**",
      },
      {
        protocol: "https",
        hostname: "**",
      },
    ],
  },
  async rewrites() {
    const apiUrl =
      process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
    const backendBaseUrl = apiUrl.replace(/\/api$/, "");

    return [
      {
        source: "/static/:path*",
        destination: `${backendBaseUrl}/static/:path*`,
      },
    ];
  },
};

export default nextConfig;
