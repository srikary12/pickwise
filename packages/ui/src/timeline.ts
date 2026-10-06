// SPDX-License-Identifier: AGPL-3.0-only
// Pure logic for EffectiveDatedTimeline: ordering, which record is current, and gaps.
// Dates are calendar dates ("YYYY-MM-DD"), compared as strings: that sorts correctly.

export interface Period {
  /** First day the record applies. */
  validFrom: string;
  /** Last day the record applies, or null while it is open-ended. */
  validTo: string | null;
}

export type TimelineState = "current" | "past" | "future";

export interface PlacedRecord<T extends Period> {
  record: T;
  state: TimelineState;
  /** True when this record and the next-older one don't meet: someone has no record in between. */
  gapBefore: boolean;
  /** True when this record starts before the next-older one has ended (shouldn't happen). */
  overlapsOlder: boolean;
}

function dayAfter(date: string): string {
  const next = new Date(`${date}T00:00:00Z`);
  next.setUTCDate(next.getUTCDate() + 1);
  return next.toISOString().slice(0, 10);
}

/** Newest first, each marked current/past/future as of `today` and flagged for gaps. */
export function placeRecords<T extends Period>(
  records: readonly T[],
  today: string,
): PlacedRecord<T>[] {
  const ordered = [...records].sort((a, b) => b.validFrom.localeCompare(a.validFrom));
  return ordered.map((record, index) => {
    const older = ordered[index + 1];
    const state: TimelineState =
      record.validFrom > today
        ? "future"
        : record.validTo === null || record.validTo >= today
          ? "current"
          : "past";
    const olderEnd = older?.validTo ?? null;
    return {
      record,
      state,
      gapBefore: older !== undefined && olderEnd !== null && dayAfter(olderEnd) < record.validFrom,
      overlapsOlder: older !== undefined && (olderEnd === null || olderEnd >= record.validFrom),
    };
  });
}
