// SPDX-License-Identifier: AGPL-3.0-only
// Lighthouse on the signed-in dashboard of a PRODUCTION build (make lighthouse).
//
// It builds the app into its own directory (so a running dev server is untouched), serves it on
// :3100 against the API of the running stack, signs in as the demo admin, and runs Lighthouse
// three times in Playwright's Chromium. The median performance score must be at least 90.
// It also checks that the components showcase (/dev/components) is absent from the build.
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";

import { chromium, request } from "@playwright/test";
import lighthouse from "lighthouse";

const PORT = 3100;
const ORIGIN = `http://localhost:${PORT}`;
const PUBLIC_ORIGIN = process.env.E2E_BASE_URL ?? "http://localhost:3000"; // what the API expects
const THRESHOLD = Number(process.env.LIGHTHOUSE_MIN_SCORE ?? 90);
const RUNS = 3;
const DEBUG_PORT = 9223;
const password = process.env.DEMO_PASSWORD;
if (!password) throw new Error("DEMO_PASSWORD is not set: run via `make lighthouse`");

const env = {
  ...process.env,
  NEXT_DIST_DIR: ".next-prod",
  PICKWISE_API_INTERNAL_URL: process.env.PICKWISE_API_INTERNAL_URL ?? "http://api:8000",
};

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { env, stdio: "inherit" });
    child.on("exit", (code) =>
      code === 0 ? resolve() : reject(new Error(`${command} exited ${code}`)),
    );
  });
}

async function waitFor(url) {
  for (let attempt = 0; attempt < 60; attempt++) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {
      // not up yet
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`${url} never came up`);
}

async function signInCookie() {
  const api = await request.newContext({ baseURL: ORIGIN });
  const csrf = await (await api.get("/api/v1/auth/csrf")).json();
  const login = await api.post("/api/v1/auth/login", {
    headers: { "X-CSRF-Token": csrf.csrf_token, Origin: PUBLIC_ORIGIN },
    data: { email: "admin@acme.test", password },
  });
  if (!login.ok()) throw new Error(`sign-in failed: ${login.status()}`);
  const state = await api.storageState();
  await api.dispose();
  return state.cookies.map((c) => `${c.name}=${c.value}`).join("; ");
}

console.log("Building the production app…");
await run("pnpm", ["exec", "next", "build"]);
const server = spawn("pnpm", ["exec", "next", "start", "--port", String(PORT)], {
  env,
  stdio: "inherit",
});
let failed = false;
try {
  await waitFor(`${ORIGIN}/healthz`);
  const cookie = await signInCookie();

  // The showcase is for development only.
  const showcase = await fetch(`${ORIGIN}/dev/components`, { headers: { cookie } });
  if (showcase.status !== 404) {
    throw new Error(`/dev/components answered ${showcase.status} in the production build`);
  }
  console.log("OK  /dev/components is 404 in the production build");

  const browser = await chromium.launch({
    args: [`--remote-debugging-port=${DEBUG_PORT}`, "--no-sandbox"],
  });
  const scores = [];
  try {
    for (let i = 0; i < RUNS; i++) {
      const result = await lighthouse(`${ORIGIN}/`, {
        port: DEBUG_PORT,
        output: "html",
        onlyCategories: ["performance"],
        extraHeaders: { Cookie: cookie },
        logLevel: "error",
      });
      const score = Math.round((result?.lhr.categories.performance?.score ?? 0) * 100);
      scores.push(score);
      const audits = result?.lhr.audits ?? {};
      console.log(
        [
          "first-contentful-paint",
          "largest-contentful-paint",
          "total-blocking-time",
          "cumulative-layout-shift",
          "speed-index",
        ]
          .map((id) => `${id}=${audits[id]?.displayValue}`)
          .join("  "),
      );
      for (const audit of Object.values(audits)) {
        if (audit.details?.type === "opportunity" && (audit.details.overallSavingsMs ?? 0) > 100) {
          console.log(
            `  opportunity: ${audit.title} (${Math.round(audit.details.overallSavingsMs)} ms)`,
          );
        }
      }
      console.log(`run ${i + 1}: performance ${score}`);
      mkdirSync("perf/report", { recursive: true });
      writeFileSync(`perf/report/run-${i + 1}.html`, String(result?.report));
    }
  } finally {
    await browser.close();
  }
  scores.sort((a, b) => a - b);
  const median = scores[Math.floor(scores.length / 2)] ?? 0;
  console.log(`median performance ${median} (needs ${THRESHOLD})`);
  if (median < THRESHOLD) failed = true;
} catch (error) {
  console.error(error);
  failed = true;
} finally {
  server.kill();
}
process.exit(failed ? 1 : 0);
