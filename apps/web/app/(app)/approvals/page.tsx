// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { Alert, Badge, Button, Card, CardContent, cn } from "@pickwise/ui";
import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { api, call, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

type Tab = "pending" | "decided" | "requested";
const TABS: readonly { id: Tab; label: string }[] = [
  { id: "pending", label: "Waiting for me" },
  { id: "decided", label: "Decided by me" },
  { id: "requested", label: "Requested by me" },
];

interface Row {
  key: string;
  requestId: string;
  title: string;
  detail: string;
  status: string;
  when: string;
}

const titleOf = (entity: string) => entity.replaceAll("_", " ");

export default function ApprovalsPage() {
  const [tab, setTab] = useState<Tab>("pending");
  const list = useInfiniteQuery({
    queryKey: ["approvals", tab],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }): Promise<{ rows: Row[]; next: string | null }> => {
      const before = pageParam ? { before: pageParam } : {};
      if (tab === "requested") {
        const page = await call(
          api().GET("/v1/approvals/requested", { params: { query: { limit: 30, ...before } } }),
        );
        return {
          next: page.next_cursor ?? null,
          rows: page.items.map((i) => ({
            key: i.request_id,
            requestId: i.request_id,
            title: i.policy_name,
            detail: `${titleOf(i.entity_type)} · step ${i.current_step} of ${i.step_count}`,
            status: i.status,
            when: i.created_at,
          })),
        };
      }
      const page = await call(
        api().GET("/v1/approvals/inbox", {
          params: { query: { state: tab, limit: 30, ...before } },
        }),
      );
      return {
        next: page.next_cursor ?? null,
        rows: page.items.map((i) => ({
          key: i.task_id,
          requestId: i.request_id,
          title: i.policy_name,
          detail: `${titleOf(i.entity_type)}${i.requester_name ? ` · from ${i.requester_name}` : ""}`,
          status: i.task_status,
          when: i.created_at,
        })),
      };
    },
    getNextPageParam: (last) => last.next ?? undefined,
  });
  const rows = list.data?.pages.flatMap((page) => page.rows) ?? [];

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">Approvals</h1>
      <div role="tablist" aria-label="Approvals" className="flex gap-1 border-b border-border">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls="approvals-panel"
            tabIndex={tab === t.id ? 0 : -1}
            onClick={() => setTab(t.id)}
            onKeyDown={(event) => {
              const index = TABS.findIndex((x) => x.id === tab);
              const next =
                event.key === "ArrowRight"
                  ? index + 1
                  : event.key === "ArrowLeft"
                    ? index - 1
                    : null;
              if (next === null) return;
              const target = TABS[(next + TABS.length) % TABS.length];
              if (!target) return;
              setTab(target.id);
              document.getElementById(`tab-${target.id}`)?.focus();
            }}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-ring",
              tab === t.id
                ? "border-primary font-medium"
                : "border-transparent text-muted-foreground",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div id="approvals-panel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
        {list.isError ? <Alert variant="destructive">{errorMessage(list.error)}</Alert> : null}
        <Card>
          <CardContent className="p-0">
            {list.isLoading ? (
              <p className="p-4 text-sm text-muted-foreground">Loading…</p>
            ) : rows.length === 0 ? (
              <p className="p-4 text-sm text-muted-foreground" data-testid="approvals-empty">
                Nothing here.
              </p>
            ) : (
              <ul className="divide-y divide-border">
                {rows.map((row) => (
                  <li key={row.key} className="flex items-center justify-between gap-4 p-4 text-sm">
                    <div className="flex flex-col gap-0.5">
                      <Link
                        href={`/approvals/${row.requestId}`}
                        className="font-medium underline-offset-4 hover:underline"
                      >
                        {row.title}
                      </Link>
                      <span className="text-muted-foreground">{row.detail}</span>
                      <span className="text-xs text-muted-foreground">
                        {formatDateTime(row.when)}
                      </span>
                    </div>
                    <Badge variant="outline">{row.status}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
        {list.hasNextPage ? (
          <div className="mt-4 text-center">
            <Button
              variant="outline"
              disabled={list.isFetchingNextPage}
              onClick={() => void list.fetchNextPage()}
            >
              Load more
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
