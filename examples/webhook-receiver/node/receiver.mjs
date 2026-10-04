// SPDX-License-Identifier: AGPL-3.0-only
// Receiving Pickwise webhooks: a complete receiver in Node.js, no dependencies.
//
// What Pickwise sends
// -------------------
// A POST with a JSON body and these headers:
//
//   X-Pickwise-Event:      the event type, e.g. "leave.request.approved"
//   X-Pickwise-Delivery:   this delivery's id; the same on every retry of it
//   X-Pickwise-Signature:  t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>
//
// The body is one event:
//
//   {"id": "<event id>", "type": "...", "created_at": "...", "tenant_id": "...",
//    "aggregate": {"type": "...", "id": "..."}, "data": {...}}
//
// What your receiver must do
// --------------------------
// 1. Verify the signature over the RAW request bytes, before parsing the JSON. Re-serialising
//    parsed JSON changes the bytes and breaks the check. (With Express, use express.raw()
//    on this route, not express.json().)
// 2. Reject timestamps more than five minutes from your clock, so a captured request can't
//    be replayed later.
// 3. Compare in constant time (crypto.timingSafeEqual).
// 4. Answer 2xx quickly (do the real work afterwards). Anything else, a timeout, or a redirect
//    counts as a failure and is retried with growing delays for about two days.
// 5. Be idempotent. Delivery is at-least-once: a retry repeats the same X-Pickwise-Delivery,
//    and a replay from the admin screen sends the same event again under a new delivery id.
//    Key your side effects on the event `id`.
//
// The signing secret (whsec_...) is shown once, when you create the endpoint or rotate its
// secret. Keep it in a secret store, not in code.
//
// Run it:  PICKWISE_WEBHOOK_SECRET=whsec_... node receiver.mjs   (listens on port 8089)

import { createHmac, timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";
import { pathToFileURL } from "node:url";

export const TOLERANCE_SECONDS = 300;
const MAX_BODY_BYTES = 1024 * 1024;

/** True if `header` is a valid, fresh signature of the raw `body` (a Buffer) under `secret`. */
export function verifySignature(
  secret,
  body,
  header,
  { now = Date.now() / 1000, toleranceSeconds = TOLERANCE_SECONDS } = {},
) {
  if (!header) return false;
  const parts = Object.fromEntries(
    header
      .split(",")
      .filter((p) => p.includes("="))
      .map((p) => [p.slice(0, p.indexOf("=")), p.slice(p.indexOf("=") + 1)]),
  );
  const timestamp = Number.parseInt(parts.t, 10);
  const received = parts.v1;
  if (!Number.isInteger(timestamp) || typeof received !== "string") return false;
  if (Math.abs(now - timestamp) > toleranceSeconds) return false;
  const expected = createHmac("sha256", secret).update(`${timestamp}.`).update(body).digest("hex");
  const a = Buffer.from(expected);
  const b = Buffer.from(received);
  return a.length === b.length && timingSafeEqual(a, b);
}

/**
 * A server that verifies, de-duplicates and then calls `onEvent(event)`.
 * The in-memory `seen` set is only for the example. In production, record processed event ids
 * in your database, in the same transaction as the side effect.
 */
export function createReceiver(secret, onEvent) {
  const seen = new Set();
  return createServer((req, res) => {
    if (req.method !== "POST") {
      res.writeHead(405).end();
      return;
    }
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        res.writeHead(413).end();
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      const body = Buffer.concat(chunks);
      if (!verifySignature(secret, body, req.headers["x-pickwise-signature"])) {
        res.writeHead(401).end("bad signature");
        return;
      }
      const event = JSON.parse(body.toString("utf8"));
      if (!seen.has(event.id)) {
        seen.add(event.id);
        onEvent(event);
      }
      res.writeHead(204).end(); // acknowledge fast; heavy work belongs in a queue
    });
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const secret = process.env.PICKWISE_WEBHOOK_SECRET;
  if (!secret) throw new Error("set PICKWISE_WEBHOOK_SECRET");
  createReceiver(secret, (event) => console.log(`${event.type} ${event.id}`)).listen(8089);
}
