import type { NextConfig } from "next";

const isProd = process.env.NODE_ENV === "production";

const nextConfig: NextConfig = {
  // Static export + basePath only for production deployment on a subpath.
  // During `next dev`, these are disabled so all routes work at root.
  ...(isProd
    ? {
        output: "export",
        basePath: "/app",
        assetPrefix: "/app",
      }
    : {}),
  images: { unoptimized: true },
  // Static assets under public/ live behind the same basePath in production.
  env: { NEXT_PUBLIC_BASE_PATH: isProd ? "/app" : "" },
  trailingSlash: true,
};

export default nextConfig;
