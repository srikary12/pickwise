// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { Button } from "@pickwise/ui";

import { MonitorIcon, MoonIcon, SunIcon } from "@/components/icons";
import { type Theme, useTheme } from "@/lib/theme";

const ORDER: readonly Theme[] = ["system", "light", "dark"];
const LABEL: Record<Theme, string> = { system: "System", light: "Light", dark: "Dark" };

/** Cycles system → light → dark. Saved in this browser only. */
export function ThemeToggle() {
  const [theme, setTheme] = useTheme();
  const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length] ?? "system";
  const Icon = theme === "dark" ? MoonIcon : theme === "light" ? SunIcon : MonitorIcon;
  return (
    <Button
      variant="ghost"
      size="icon"
      data-testid="theme-toggle"
      aria-label={`Theme: ${LABEL[theme]}. Switch to ${LABEL[next]}`}
      onClick={() => setTheme(next)}
    >
      <Icon />
    </Button>
  );
}
