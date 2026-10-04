// SPDX-License-Identifier: AGPL-3.0-only
// Runs the example receiver against a signature produced by Pickwise's real signer
// (vector.json is checked by the backend tests, so it can't drift from the signer).
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { once } from "node:events";
import { test } from "node:test";

import { createReceiver, verifySignature } from "./receiver.mjs";

const vector = JSON.parse(readFileSync(new URL("../vector.json", import.meta.url), "utf8"));
const body = Buffer.from(vector.body, "utf8");
const at = (offset) => ({ now: vector.timestamp + offset });

test("accepts the signature Pickwise produced", () => {
  assert.equal(verifySignature(vector.secret, body, vector.signature, at(10)), true);
});

test("rejects a wrong secret, an altered body and malformed headers", () => {
  assert.equal(verifySignature("whsec_other", body, vector.signature, at(10)), false);
  assert.equal(
    verifySignature(
      vector.secret,
      Buffer.concat([body, Buffer.from(" ")]),
      vector.signature,
      at(10),
    ),
    false,
  );
  for (const header of [
    undefined,
    "",
    "v1=abc",
    "t=abc,v1=abc",
    "garbage",
    `t=${vector.timestamp},v1=short`,
  ]) {
    assert.equal(verifySignature(vector.secret, body, header, at(10)), false, String(header));
  }
});

test("rejects timestamps outside the tolerance, in both directions", () => {
  assert.equal(verifySignature(vector.secret, body, vector.signature, at(301)), false);
  assert.equal(verifySignature(vector.secret, body, vector.signature, at(-301)), false);
  assert.equal(verifySignature(vector.secret, body, vector.signature, at(299)), true);
});

function sign(secret, timestamp, payload) {
  const mac = createHmac("sha256", secret).update(`${timestamp}.`).update(payload).digest("hex");
  return `t=${timestamp},v1=${mac}`;
}

test("the server verifies, acknowledges and processes each event once", async () => {
  const events = [];
  const server = createReceiver(vector.secret, (event) => events.push(event));
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const url = `http://127.0.0.1:${server.address().port}/`;
  const now = Math.floor(Date.now() / 1000);
  const post = (payload, signature) =>
    fetch(url, { method: "POST", body: payload, headers: { "X-Pickwise-Signature": signature } });
  try {
    const payload = Buffer.from(JSON.stringify({ id: "evt_1", type: "leave.request.approved" }));
    assert.equal((await post(payload, sign(vector.secret, now, payload))).status, 204);
    // A retry (or a replay) of the same event is acknowledged but not processed again.
    assert.equal((await post(payload, sign(vector.secret, now, payload))).status, 204);
    assert.equal(events.length, 1);
    // A forged or stale request is refused and never reaches the handler.
    assert.equal((await post(payload, sign("whsec_forged", now, payload))).status, 401);
    assert.equal((await post(payload, sign(vector.secret, now - 3600, payload))).status, 401);
    assert.equal(events.length, 1);
  } finally {
    server.close();
  }
});
