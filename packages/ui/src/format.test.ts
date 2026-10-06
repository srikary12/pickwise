// SPDX-License-Identifier: AGPL-3.0-only
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  formatCompactCurrency,
  formatCurrency,
  formatDate,
  formatDateTime,
  formatNumber,
  formatPeriod,
} from "./format.ts";

test("numbers use lakh and crore grouping", () => {
  assert.equal(formatNumber(1234567.5), "12,34,567.5");
  assert.equal(formatNumber("123456789"), "12,34,56,789");
  assert.equal(formatNumber(999), "999");
});

test("currency is rupees with paise, from a number or a decimal string", () => {
  assert.equal(formatCurrency("150000"), "₹1,50,000.00");
  assert.equal(formatCurrency(1234.5), "₹1,234.50");
  assert.equal(formatCurrency("-500"), "-₹500.00");
  assert.equal(formatCurrency("150000", { whole: true }), "₹1,50,000");
  // No float round trip: 17 significant digits survive.
  assert.equal(formatCurrency("12345678901234.56"), "₹1,23,45,67,89,01,234.56");
});

test("compact currency uses lakh and crore", () => {
  assert.equal(formatCompactCurrency(1250000), "₹12.5L");
  assert.equal(formatCompactCurrency(30000000), "₹3Cr");
});

test("a calendar date never shifts with the timezone", () => {
  assert.equal(formatDate("2026-04-01"), "1 Apr 2026");
  assert.equal(formatDate("2026-04-01", "America/Los_Angeles"), "1 Apr 2026");
  assert.equal(formatDate("2026-12-31"), "31 Dec 2026");
});

test("an instant is shown in the company timezone", () => {
  // 20:00 UTC on 31 March is already 1 April in India (UTC+5:30).
  assert.equal(formatDate("2026-03-31T20:00:00Z"), "1 Apr 2026");
  assert.equal(formatDate("2026-03-31T20:00:00Z", "UTC"), "31 Mar 2026");
  assert.match(formatDateTime("2026-04-01T09:00:00Z"), /^1 Apr 2026, 2:30\s?pm$/i);
});

test("a period can be open-ended", () => {
  assert.equal(formatPeriod("2026-04-01", "2027-03-31"), "1 Apr 2026 – 31 Mar 2027");
  assert.equal(formatPeriod("2026-04-01", null), "1 Apr 2026 – present");
});
