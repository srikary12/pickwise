// SPDX-License-Identifier: AGPL-3.0-only
// Liveness for the container healthcheck. It deliberately doesn't call the API:
// the API has its own healthcheck, and probing through it doubles the log noise.
export const dynamic = "force-dynamic";

export function GET(): Response {
  return Response.json({ status: "ok" });
}
