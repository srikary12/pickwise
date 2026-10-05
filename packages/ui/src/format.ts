// SPDX-License-Identifier: AGPL-3.0-only
// en-IN formatting: lakh/crore digit grouping, ₹ amounts and dates in the company's timezone.
// Money arrives from the API as decimal strings and is formatted without going through a float.

export const DEFAULT_TIME_ZONE = "Asia/Kolkata";
const LOCALE = "en-IN";

const NUMBER = new Intl.NumberFormat(LOCALE);

/** 1234567.5 → "12,34,567.5". Accepts a decimal string so large values keep their digits. */
export function formatNumber(value: number | string): string {
  return NUMBER.format(value as number);
}

export interface CurrencyOptions {
  currency?: string;
  /** Show whole rupees ("₹1,50,000") instead of paise. */
  whole?: boolean;
}

/** "150000" → "₹1,50,000.00". Negative amounts keep the sign: "-₹500.00". */
export function formatCurrency(value: number | string, options: CurrencyOptions = {}): string {
  const { currency = "INR", whole = false } = options;
  return new Intl.NumberFormat(LOCALE, {
    style: "currency",
    currency,
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: whole ? 0 : 2,
  }).format(value as number);
}

/** For tiles and charts: 1250000 → "₹12.5L", 30000000 → "₹3Cr". */
export function formatCompactCurrency(value: number | string, currency = "INR"): string {
  return new Intl.NumberFormat(LOCALE, {
    style: "currency",
    currency,
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value as number);
}

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

/** A calendar date has no time or zone, so "2026-04-01" must never shift to March 31. */
function toDate(value: string | Date): { date: Date; timeZone: string | undefined } {
  if (value instanceof Date) return { date: value, timeZone: undefined };
  const match = DATE_ONLY.exec(value);
  if (match) {
    const [, year, month, day] = match;
    return {
      date: new Date(Date.UTC(Number(year), Number(month) - 1, Number(day))),
      timeZone: "UTC",
    };
  }
  return { date: new Date(value), timeZone: undefined };
}

/** "2026-04-01" → "1 Apr 2026". Instants are shown in `timeZone` (default Asia/Kolkata). */
export function formatDate(value: string | Date, timeZone = DEFAULT_TIME_ZONE): string {
  const { date, timeZone: fixed } = toDate(value);
  return new Intl.DateTimeFormat(LOCALE, {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: fixed ?? timeZone,
  }).format(date);
}

/** "1 Apr 2026, 2:30 pm" in `timeZone` (default Asia/Kolkata). */
export function formatDateTime(value: string | Date, timeZone = DEFAULT_TIME_ZONE): string {
  const { date } = toDate(value);
  return new Intl.DateTimeFormat(LOCALE, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  }).format(date);
}

/** A half-open period: "1 Apr 2026 – 31 Mar 2027", or "1 Apr 2026 – present" when open. */
export function formatPeriod(start: string, end: string | null): string {
  return `${formatDate(start)} – ${end ? formatDate(end) : "present"}`;
}
