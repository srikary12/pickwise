// SPDX-License-Identifier: AGPL-3.0-only
"use client";

// A tiny store for "this record changed under you" (HTTP 409 stale_row_version). The query
// client reports into it and <ConflictDialog> reads it, so every page gets the same prompt.
import { useSyncExternalStore } from "react";

let open = false;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

export function showConflict() {
  if (open) return;
  open = true;
  emit();
}

export function dismissConflict() {
  if (!open) return;
  open = false;
  emit();
}

export function useConflictOpen(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => open,
    () => false,
  );
}
