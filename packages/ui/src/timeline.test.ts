// SPDX-License-Identifier: AGPL-3.0-only
import assert from "node:assert/strict";
import { test } from "node:test";

import { placeRecords } from "./timeline.ts";

const rec = (validFrom: string, validTo: string | null) => ({ validFrom, validTo });

test("records are newest first and the open one is current", () => {
  const placed = placeRecords(
    [rec("2024-04-01", "2025-03-31"), rec("2025-04-01", null), rec("2023-04-01", "2024-03-31")],
    "2026-01-15",
  );
  assert.deepEqual(
    placed.map((p) => [p.record.validFrom, p.state]),
    [
      ["2025-04-01", "current"],
      ["2024-04-01", "past"],
      ["2023-04-01", "past"],
    ],
  );
  assert.ok(placed.every((p) => !p.gapBefore && !p.overlapsOlder));
});

test("a record that starts in the future is future, and today is inside a closed period's last day", () => {
  const placed = placeRecords(
    [rec("2026-04-01", null), rec("2025-04-01", "2026-03-31")],
    "2026-03-31",
  );
  assert.deepEqual(
    placed.map((p) => p.state),
    ["future", "current"],
  );
});

test("a gap between consecutive records is flagged on the newer one", () => {
  const placed = placeRecords(
    [rec("2025-06-01", null), rec("2024-04-01", "2025-03-31")],
    "2026-01-01",
  );
  assert.equal(placed[0]?.gapBefore, true);
  assert.equal(placed[1]?.gapBefore, false);
});

test("back-to-back records have no gap even across a month or year end", () => {
  const placed = placeRecords(
    [rec("2025-01-01", null), rec("2024-01-01", "2024-12-31")],
    "2026-01-01",
  );
  assert.equal(placed[0]?.gapBefore, false);
  assert.equal(placed[0]?.overlapsOlder, false);
});

test("overlapping records are flagged", () => {
  const placed = placeRecords(
    [rec("2025-01-01", null), rec("2024-01-01", "2025-02-01")],
    "2026-01-01",
  );
  assert.equal(placed[0]?.overlapsOlder, true);
});
