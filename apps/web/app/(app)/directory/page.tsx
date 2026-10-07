// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { DirectoryEntry, DirectoryPage, OrgChart, OrgChartNode } from "@pickwise/api-client";
import { DataTable, type DataTableColumn, Input, Label, Tabs } from "@pickwise/ui";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useDeferredValue, useId, useMemo, useState } from "react";

import { NoAccess } from "@/components/no-access";
import { api, call, errorMessage } from "@/lib/api";
import { can, useSession } from "@/lib/session";

const TABS = [
  { id: "people", label: "People" },
  { id: "chart", label: "Org chart" },
] as const;

function People() {
  const [q, setQ] = useState("");
  const search = useDeferredValue(q);
  const id = useId();
  const list = useInfiniteQuery({
    queryKey: ["directory", { q: search }],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      call<DirectoryPage>(
        api().GET("/v1/directory", {
          params: {
            query: {
              limit: 50,
              ...(search ? { q: search } : {}),
              ...(pageParam ? { cursor: pageParam } : {}),
            },
          },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const rows = list.data?.pages.flatMap((p) => p.items) ?? [];
  const columns: DataTableColumn<DirectoryEntry>[] = [
    {
      id: "name",
      header: "Name",
      hideable: false,
      cell: (e) => (
        <>
          <span className="font-medium">{e.display_name}</span>
          <div className="text-xs text-muted-foreground">{e.employee_code}</div>
        </>
      ),
    },
    { id: "designation", header: "Designation", cell: (e) => e.designation_name ?? "—" },
    { id: "department", header: "Department", cell: (e) => e.department_name ?? "—" },
    { id: "location", header: "Location", cell: (e) => e.location_name ?? "—" },
    { id: "manager", header: "Manager", cell: (e) => e.manager_name ?? "—" },
    { id: "email", header: "Email", cell: (e) => e.work_email ?? "—" },
  ];
  return (
    <DataTable
      caption="Directory"
      columns={columns}
      rows={rows}
      getRowId={(e) => e.id}
      rowTestId={(e) => `person-${e.employee_code}`}
      loading={list.isLoading}
      error={list.isError ? errorMessage(list.error) : null}
      emptyMessage="No one matches."
      hasMore={Boolean(list.hasNextPage)}
      loadingMore={list.isFetchingNextPage}
      onLoadMore={() => void list.fetchNextPage()}
      toolbar={
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={id}>Search</Label>
          <Input
            id={id}
            value={q}
            onChange={(event) => setQ(event.target.value)}
            placeholder="Name, code or email"
            className="w-64"
          />
        </div>
      }
    />
  );
}

function Chart() {
  const chart = useQuery({
    queryKey: ["directory", "org-chart"],
    queryFn: () => call<OrgChart>(api().GET("/v1/directory/org-chart")),
  });
  const { roots, children } = useMemo(() => {
    const nodes = chart.data?.nodes ?? [];
    const known = new Set(nodes.map((n) => n.id));
    const byParent = new Map<string, OrgChartNode[]>();
    const top: OrgChartNode[] = [];
    for (const node of nodes) {
      if (node.manager_employee_id && known.has(node.manager_employee_id)) {
        const list = byParent.get(node.manager_employee_id) ?? [];
        list.push(node);
        byParent.set(node.manager_employee_id, list);
      } else top.push(node);
    }
    return { roots: top, children: byParent };
  }, [chart.data]);

  function render(node: OrgChartNode) {
    const kids = children.get(node.id) ?? [];
    const body = (
      <>
        <span className="font-medium">{node.display_name}</span>
        <span className="ml-2 text-xs text-muted-foreground">
          {[node.designation_name, node.department_name].filter(Boolean).join(" · ")}
          {node.report_count > 0
            ? ` · ${node.report_count} direct report${node.report_count === 1 ? "" : "s"}`
            : ""}
        </span>
      </>
    );
    return (
      <li key={node.id}>
        {kids.length > 0 ? (
          <details open={node.manager_employee_id === null}>
            <summary
              className="cursor-pointer rounded px-1 py-0.5 hover:bg-muted"
              data-testid={`chart-${node.display_name}`}
            >
              {body}
            </summary>
            <ul className="ml-5 border-l border-border pl-3">{kids.map(render)}</ul>
          </details>
        ) : (
          <div className="px-1 py-0.5 pl-5" data-testid={`chart-${node.display_name}`}>
            {body}
          </div>
        )}
      </li>
    );
  }
  if (chart.isError)
    return (
      <p role="alert" className="text-sm text-destructive">
        {errorMessage(chart.error)}
      </p>
    );
  if (!chart.data)
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading…
      </p>
    );
  return roots.length === 0 ? (
    <p className="text-sm text-muted-foreground">Nobody to show yet.</p>
  ) : (
    <ul aria-label="Reporting structure" className="flex flex-col gap-1 text-sm">
      {roots.map(render)}
    </ul>
  );
}

function Directory() {
  const router = useRouter();
  const params = useSearchParams();
  const { data: session } = useSession();
  const tab = TABS.find((t) => t.id === params.get("tab"))?.id ?? "people";
  if (!session) return null;
  if (!can(session, "core.directory.read")) return <NoAccess />;
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">Directory</h1>
      <Tabs
        label="Directory"
        tabs={TABS}
        value={tab}
        onValueChange={(id) => router.replace(`/directory?tab=${id}`)}
      >
        {tab === "people" ? <People /> : <Chart />}
      </Tabs>
    </div>
  );
}

export default function DirectoryPage() {
  return (
    <Suspense>
      <Directory />
    </Suspense>
  );
}
