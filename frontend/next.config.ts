import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  images: {
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
