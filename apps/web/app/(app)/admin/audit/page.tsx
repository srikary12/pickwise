// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { AuditEventOut } from "@pickwise/api-client";
import {
  Button,
  Card,
  CardContent,
  DataTable,
  type DataTableColumn,
  Field,
  formatDateTime,
  Input,
} from "@pickwise/ui";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";

import { NoAccess } from "@/components/no-access";
import { api, call, errorMessage } from "@/lib/api";
import { can, useSession } from "@/lib/session";

type Filters = { action: string; entity_table: string; since: string; until: string };
const NONE: Filters = { action: "", entity_table: "", since: "", until: "" };

function query(filters: Filters): Record<string, string> {
  const out: Record<string, string> = {};
  if (filters.action) out.action = filters.action;
  if (filters.entity_table) out.entity_table = filters.entity_table;
  // Date inputs are local dates: the whole of the first day to the end of the last.
  if (filters.since) out.since = new Date(`${filters.since}T00:00:00`).toISOString();
  if (filters.until) {
    const end = new Date(`${filters.until}T00:00:00`);
    end.setDate(end.getDate() + 1);
    out.until = end.toISOString();
  }
  return out;
}

const COLUMNS: DataTableColumn<AuditEventOut>[] = [
  {
    id: "when",
    header: "When",
    cell: (e) => <span className="whitespace-nowrap text-xs">{formatDateTime(e.occurred_at)}</span>,
    hideable: false,
  },
  { id: "who", header: "Who", cell: (e) => e.actor_name ?? e.actor_type },
  {
    id: "action",
    header: "Action",
    cell: (e) => <span className="font-mono text-xs">{e.action}</span>,
    hideable: false,
  },
  {
    id: "record",
    header: "Record",
    cell: (e) => (
      <span className="font-mono text-xs">
        {e.entity_table ? `${e.entity_schema}.${e.entity_table}` : ""}
      </span>
    ),
  },
  {
    id: "changes",
    header: "Changes",
    cell: (e) =>
      e.changes ? (
        <details>
          <summary className="cursor-pointer text-xs underline">Show</summary>
          <pre className="max-w-md overflow-auto text-xs">{JSON.stringify(e.changes, null, 2)}</pre>
        </details>
      ) : null,
  },
];

export default function AuditPage() {
  const { data: session } = useSession();
  const [draft, setDraft] = useState<Filters>(NONE);
  const [applied, setApplied] = useState<Filters>(NONE);
  const params = query(applied);

  const events = useInfiniteQuery({
    queryKey: ["admin", "audit", params],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      call(
        api().GET("/v1/audit/events", {
          params: { query: { ...params, limit: 50, ...(pageParam ? { cursor: pageParam } : {}) } },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: can(session, "platform.audit.read"),
  });

  if (!can(session, "platform.audit.read")) return <NoAccess />;
  const items: AuditEventOut[] = events.data?.pages.flatMap((p) => p.items) ?? [];
  const exportUrl = `/api/v1/audit/events/export?${new URLSearchParams(params).toString()}`;

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Audit trail</h1>
      <Card>
        <CardContent className="pt-6">
          <form
            className="grid gap-4 sm:grid-cols-4"
            onSubmit={(event) => {
              event.preventDefault();
              setApplied(draft);
            }}
          >
            <Field id="audit-action" label="Action">
              <Input
                id="audit-action"
                placeholder="login or custom_field.*"
                value={draft.action}
                onChange={(e) => setDraft({ ...draft, action: e.target.value })}
              />
            </Field>
            <Field id="audit-entity" label="Table">
              <Input
                id="audit-entity"
                placeholder="platform.roles"
                value={draft.entity_table}
                onChange={(e) => setDraft({ ...draft, entity_table: e.target.value })}
              />
            </Field>
            <Field id="audit-since" label="From">
              <Input
                id="audit-since"
                type="date"
                value={draft.since}
                onChange={(e) => setDraft({ ...draft, since: e.target.value })}
              />
            </Field>
            <Field id="audit-until" label="To">
              <Input
                id="audit-until"
                type="date"
                value={draft.until}
                onChange={(e) => setDraft({ ...draft, until: e.target.value })}
              />
            </Field>
            <div className="flex gap-3 sm:col-span-4">
              <Button type="submit">Apply filters</Button>
              <Button
                variant="outline"
                onClick={() => {
                  setDraft(NONE);
                  setApplied(NONE);
                }}
              >
                Clear
              </Button>
              {can(session, "platform.audit.export") ? (
                <a
                  href={exportUrl}
                  download
                  data-testid="audit-export"
                  className="ml-auto inline-flex items-center text-sm underline underline-offset-4"
                >
                  Export CSV
                </a>
              ) : null}
            </div>
          </form>
        </CardContent>
      </Card>
      <DataTable
        caption="Audit events, newest first"
        columns={COLUMNS}
        rows={items}
        getRowId={(e) => e.id}
        rowTestId={() => "audit-row"}
        loading={events.isLoading}
        error={events.error ? errorMessage(events.error) : null}
        emptyMessage="No events match."
        hasMore={events.hasNextPage}
        loadingMore={events.isFetchingNextPage}
        onLoadMore={() => void events.fetchNextPage()}
      />
    </div>
  );
}
