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
};

export default nextConfig;
