// SPDX-License-Identifier: AGPL-3.0-only
// en-IN number, currency and date formatting. The full set (and its tests) lands with the
// shared components in Phase 4b; the shell only needs date-times so far.
const DATE_TIME = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" });

export function formatDateTime(value: string | Date): string {
  return DATE_TIME.format(typeof value === "string" ? new Date(value) : value);
}
