// SPDX-License-Identifier: AGPL-3.0-only
import path from "node:path";
import { fileURLToPath } from "node:url";

import type { NextConfig } from "next";

const repoRoot = path.join(path.dirname(fileURLToPath(import.meta.url)), "../..");

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: repoRoot,
  transpilePackages: ["@pickwise/api-client", "@pickwise/ui"],
  poweredByHeader: false,
  reactStrictMode: true,
  // Same origin for the browser (ADR 0010): /api/* is proxied to the API, so
  // session cookies are first-party and no CORS is needed. Prod does the same in Caddy.
  async rewrites() {
    const api = process.env.PICKWISE_API_INTERNAL_URL ?? "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${api}/:path*` }];
  },
};

export default nextConfig;
