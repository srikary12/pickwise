// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { SearchOut, SessionState } from "@pickwise/api-client";
import { cn } from "@pickwise/ui";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { api, call } from "@/lib/api";
import { NAV_ITEMS } from "@/lib/nav";
import { can } from "@/lib/session";

interface Entry {
  id: string;
  group: string;
  title: string;
  subtitle?: string | null;
  href: string;
}

const MIN_QUERY = 2;

/**
 * Ctrl/Cmd+K: jump to a page, or search people the caller is allowed to see. Results come from
 * GET /v1/search, which each module feeds and filters by permission and data scope.
 * ARIA combobox pattern: focus stays in the input, `aria-activedescendant` names the active row.
 */
export function CommandPalette({
  session,
  open,
  onClose,
}: {
  session: SessionState;
  open: boolean;
  onClose: () => void;
}) {
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [text, setText] = useState("");
  const [debounced, setDebounced] = useState("");
  const [active, setActive] = useState(0);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) {
      element.showModal();
      input.current?.focus();
    }
    if (!open && element.open) element.close();
  }, [open]);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(text.trim()), 200);
    return () => clearTimeout(timer);
  }, [text]);

  const searching = open && debounced.length >= MIN_QUERY;
  const results = useQuery({
    queryKey: ["search", debounced],
    queryFn: () =>
      call<SearchOut>(api().GET("/v1/search", { params: { query: { q: debounced } } })),
    enabled: searching,
    staleTime: 10_000,
  });

  const entries = useMemo<Entry[]>(() => {
    const needle = text.trim().toLowerCase();
    const pages = NAV_ITEMS.filter(
      (item) =>
        (!item.permission || can(session, item.permission)) &&
        (needle === "" || item.label.toLowerCase().includes(needle)),
    ).map((item) => ({
      id: `page-${item.href}`,
      group: "Pages",
      title: item.label,
      href: item.href,
    }));
    const found = (searching ? (results.data?.groups ?? []) : []).flatMap((group) =>
      group.hits.map((hit) => ({
        id: `hit-${hit.kind}-${hit.id}`,
        group: group.label,
        title: hit.title,
        subtitle: hit.subtitle,
        href: hit.link,
      })),
    );
    return [...found, ...pages];
  }, [text, session, searching, results.data]);

  const current = Math.min(active, Math.max(entries.length - 1, 0));

  function close() {
    setText("");
    setDebounced("");
    onClose();
  }

  function go(entry: Entry | undefined) {
    if (!entry) return;
    close();
    router.push(entry.href);
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive(Math.min(current + 1, entries.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive(Math.max(current - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      go(entries[current]);
    }
  }

  let lastGroup = "";
  return (
    <dialog
      ref={dialog}
      aria-label="Search"
      onClose={close}
      onClick={(event) => {
        if (event.target === dialog.current) close(); // a click on the backdrop
      }}
      className="mx-auto mt-[12vh] w-full max-w-lg rounded-xl border border-border bg-card p-0 text-card-foreground shadow-lg backdrop:bg-black/40"
    >
      <input
        ref={input}
        role="combobox"
        aria-expanded="true"
        aria-controls="palette-results"
        aria-activedescendant={entries[current] ? entries[current].id : undefined}
        aria-label="Search pages and people"
        data-testid="palette-input"
        autoComplete="off"
        placeholder="Search pages and people…"
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          setActive(0);
        }}
        onKeyDown={onKeyDown}
        className="w-full rounded-t-xl border-b border-border bg-transparent px-4 py-3 text-sm outline-none focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
      />
      <div
        id="palette-results"
        role="listbox"
        aria-label="Results"
        className="max-h-80 overflow-y-auto p-2"
      >
        {entries.map((entry, index) => {
          const heading = entry.group !== lastGroup;
          lastGroup = entry.group;
          return (
            <div key={entry.id} role="presentation">
              {heading ? (
                <div
                  role="presentation"
                  className="px-2 pb-1 pt-2 text-xs font-medium text-muted-foreground"
                >
                  {entry.group}
                </div>
              ) : null}
              <div
                id={entry.id}
                role="option"
                aria-selected={index === current}
                onMouseMove={() => setActive(index)}
                onClick={() => go(entry)}
                className={cn(
                  "flex cursor-pointer flex-col rounded-md px-2 py-1.5 text-sm",
                  index === current && "bg-muted",
                )}
              >
                <span>{entry.title}</span>
                {entry.subtitle ? (
                  <span className="text-xs text-muted-foreground">{entry.subtitle}</span>
                ) : null}
              </div>
            </div>
          );
        })}
        {entries.length === 0 ? (
          <p className="p-3 text-sm text-muted-foreground" role="status">
            {results.isFetching ? "Searching…" : "No matches."}
          </p>
        ) : null}
      </div>
      <p className="border-t border-border px-3 py-1.5 text-xs text-muted-foreground">
        ↑ ↓ to move, Enter to open, Esc to close
      </p>
    </dialog>
  );
}
