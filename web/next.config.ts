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
  trailingSlash: true,
};

export default nextConfig;
