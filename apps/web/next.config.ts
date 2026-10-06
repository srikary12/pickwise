// SPDX-License-Identifier: AGPL-3.0-only
import path from "node:path";
import { fileURLToPath } from "node:url";

import type { NextConfig } from "next";

const repoRoot = path.join(path.dirname(fileURLToPath(import.meta.url)), "../..");

const nextConfig: NextConfig = {
  output: "standalone",
  // make lighthouse builds into its own directory so a running dev server is left alone.
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  outputFileTracingRoot: repoRoot,
  transpilePackages: ["@pickwise/api-client", "@pickwise/ui"],
  poweredByHeader: false,
  reactStrictMode: true,
  // Same origin for the browser (ADR 0010): /api/* is proxied to the API, so
  // session cookies are first-party and no CORS is needed. Prod does the same in Caddy.
  async rewrites() {
    const api = process.env.PICKWISE_API_INTERNAL_URL ?? "http://localhost:8000";
    return {
      // The components showcase is a development aid. A production build answers a real 404
      // (a page-level notFound() would still be served with status 200 once prerendered).
      beforeFiles:
        process.env.NODE_ENV === "production"
          ? [{ source: "/dev/:path*", destination: "/not-found-in-production" }]
          : [],
      afterFiles: [{ source: "/api/:path*", destination: `${api}/:path*` }],
      fallback: [],
    };
  },
};

export default nextConfig;
