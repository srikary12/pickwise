// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { EmployeeOut, EmployeePage } from "@pickwise/api-client";
import {
  Badge,
  buttonVariants,
  DataTable,
  type DataTableColumn,
  formatDate,
  Input,
  Label,
  Select,
} from "@pickwise/ui";
import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useDeferredValue, useId, useState } from "react";

import { NoAccess } from "@/components/no-access";
import { api, call, errorMessage } from "@/lib/api";
import { can, useSession } from "@/lib/session";

const STATUSES = ["draft", "pre_boarding", "active", "notice_period", "leave_of_absence", "exited"];
const VARIANT = {
  active: "success",
  draft: "outline",
  pre_boarding: "warning",
  notice_period: "warning",
  leave_of_absence: "outline",
  exited: "destructive",
} as const;

export default function EmployeesPage() {
  const { data: session } = useSession();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const search = useDeferredValue(q);
  const searchId = useId();
  const statusId = useId();
  const allowed = can(session, "core.employees.read");

  const list = useInfiniteQuery({
    queryKey: ["employees", { q: search, status }],
    enabled: allowed,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      call<EmployeePage>(
        api().GET("/v1/employees", {
          params: {
            query: {
              limit: 50,
              ...(search ? { q: search } : {}),
              ...(status ? { status: status as EmployeeOut["status"] } : {}),
              ...(pageParam ? { cursor: pageParam } : {}),
            },
          },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const rows = list.data?.pages.flatMap((p) => p.items) ?? [];

  const columns: DataTableColumn<EmployeeOut>[] = [
    {
      id: "name",
      header: "Employee",
      hideable: false,
      cell: (e) => (
        <>
          <Link
            href={`/employees/${e.id}`}
            className="font-medium underline-offset-2 hover:underline"
          >
            {e.display_name}
          </Link>
          <div className="text-xs text-muted-foreground">{e.employee_code}</div>
        </>
      ),
    },
    { id: "designation", header: "Designation", cell: (e) => e.job?.designation_name ?? "—" },
    { id: "department", header: "Department", cell: (e) => e.job?.department_name ?? "—" },
    {
      id: "location",
      header: "Location",
      cell: (e) => e.job?.location_name ?? "—",
      defaultHidden: true,
    },
    { id: "manager", header: "Manager", cell: (e) => e.job?.manager_name ?? "—" },
    { id: "joined", header: "Joined", cell: (e) => formatDate(e.date_of_joining) },
    {
      id: "status",
      header: "Status",
      cell: (e) => <Badge variant={VARIANT[e.status]}>{e.status.replace("_", " ")}</Badge>,
    },
  ];

  if (!session) return null;
  if (!allowed) return <NoAccess />;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Employees</h1>
        {can(session, "core.employees.create") ? (
          <Link href="/employees/new" className={buttonVariants()}>
            Add employee
          </Link>
        ) : null}
      </div>
      <DataTable
        caption="Employees"
        columns={columns}
        rows={rows}
        getRowId={(e) => e.id}
        rowTestId={(e) => `employee-${e.employee_code}`}
        loading={list.isLoading}
        error={list.isError ? errorMessage(list.error) : null}
        emptyMessage="No employees match."
        hasMore={Boolean(list.hasNextPage)}
        loadingMore={list.isFetchingNextPage}
        onLoadMore={() => void list.fetchNextPage()}
        toolbar={
          <>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={searchId}>Search</Label>
              <Input
                id={searchId}
                value={q}
                onChange={(event) => setQ(event.target.value)}
                placeholder="Name, code or email"
                className="w-64"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={statusId}>Status</Label>
              <Select
                id={statusId}
                value={status}
                onChange={(event) => setStatus(event.target.value)}
              >
                <option value="">All</option>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s.replace("_", " ")}
                  </option>
                ))}
              </Select>
            </div>
          </>
        }
      />
    </div>
  );
}
