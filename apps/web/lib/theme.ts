// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useCallback, useSyncExternalStore } from "react";

import { THEME_KEY as KEY } from "./theme-script";

export type Theme = "light" | "dark" | "system";

const listeners = new Set<() => void>();

function readTheme(): Theme {
  try {
    const stored = localStorage.getItem(KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // Storage can be blocked (private windows, site settings): fall back to the system theme.
  }
  return "system";
}

function apply(theme: Theme) {
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
  try {
    if (theme === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, theme);
  } catch {
    // Not persisted; the choice still holds for this page view.
  }
  for (const listener of listeners) listener();
}

export function useTheme(): [Theme, (theme: Theme) => void] {
  const theme = useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    readTheme,
    () => "system" as Theme,
  );
  const set = useCallback((next: Theme) => apply(next), []);
  return [theme, set];
}
