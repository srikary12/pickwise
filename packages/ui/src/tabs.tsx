// SPDX-License-Identifier: AGPL-3.0-only
"use client";

// Tabs per the WAI-ARIA pattern: one tab stop, Left/Right/Home/End move between tabs, and the
// active tab's panel is the only one rendered. The caller owns `value`, so the active tab can
// live in the URL.
import { type KeyboardEvent, type ReactNode, useId, useRef } from "react";

import { cn } from "./lib/utils";

export interface TabItem {
  id: string;
  label: string;
}

export function Tabs({
  label,
  tabs,
  value,
  onValueChange,
  children,
  className,
}: {
  /** Names the tab list for screen readers. */
  label: string;
  tabs: readonly TabItem[];
  value: string;
  onValueChange: (id: string) => void;
  /** The active tab's content. */
  children: ReactNode;
  className?: string;
}) {
  const base = useId();
  const refs = useRef(new Map<string, HTMLButtonElement>());
  const tabId = (id: string) => `${base}-tab-${id}`;
  const panelId = `${base}-panel`;

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const index = tabs.findIndex((tab) => tab.id === value);
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    event.preventDefault();
    const target = tabs[next];
    if (!target) return;
    onValueChange(target.id);
    refs.current.get(target.id)?.focus();
  }

  return (
    <div className={className}>
      <div
        role="tablist"
        aria-label={label}
        onKeyDown={onKeyDown}
        className="flex gap-1 overflow-x-auto border-b border-border"
      >
        {tabs.map((tab) => {
          const selected = tab.id === value;
          return (
            <button
              key={tab.id}
              ref={(node) => {
                if (node) refs.current.set(tab.id, node);
                else refs.current.delete(tab.id);
              }}
              type="button"
              role="tab"
              id={tabId(tab.id)}
              aria-selected={selected}
              aria-controls={selected ? panelId : undefined}
              tabIndex={selected ? 0 : -1}
              onClick={() => onValueChange(tab.id)}
              className={cn(
                "-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring",
                selected
                  ? "border-primary font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {tab.label}
            </button>
          );
        })}
      </div>
      <div role="tabpanel" id={panelId} aria-labelledby={tabId(value)} className="pt-4">
        {children}
      </div>
    </div>
  );
}
