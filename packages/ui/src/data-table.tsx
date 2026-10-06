// SPDX-License-Identifier: AGPL-3.0-only
"use client";

// A table for server-side lists: the API owns sorting, filtering and paging (cursor pagination),
// so this component only renders rows, reports what the user asked for, and shows the states
// (loading, empty, error, "load more"). TanStack Table supplies the column and visibility model.
import {
  type ColumnDef,
  columnVisibilityFeature,
  rowSortingFeature,
  type SortingState,
  tableFeatures,
  useTable,
} from "@tanstack/react-table";
import { type ReactNode, useId, useMemo, useState } from "react";

import { Alert } from "./alert";
import { Button } from "./button";
import { cn } from "./lib/utils";

const features = tableFeatures({ columnVisibilityFeature, rowSortingFeature });

export interface DataTableColumn<T extends object> {
  /** Stable id; also the key sent to `onSortChange` (the API's sort field). */
  id: string;
  header: string;
  cell: (row: T) => ReactNode;
  /** Sortable columns need `onSortChange` on the table, and the API to honour it. */
  sortable?: boolean;
  /** Columns the user may hide from the "Columns" menu. Default true. */
  hideable?: boolean;
  /** Hidden until the user turns it on. */
  defaultHidden?: boolean;
  className?: string;
}

export interface Sort {
  id: string;
  desc: boolean;
}

export interface DataTableProps<T extends object> {
  /** The accessible name of the table, announced by screen readers. */
  caption: string;
  columns: readonly DataTableColumn<T>[];
  rows: readonly T[];
  getRowId: (row: T) => string;
  /** Becomes the row's `data-testid` (for tests). */
  rowTestId?: (row: T) => string;
  sort?: Sort | null;
  onSortChange?: (sort: Sort | null) => void;
  loading?: boolean;
  error?: string | null;
  emptyMessage?: string;
  /** Filters and actions shown above the table, left of the columns menu. */
  toolbar?: ReactNode;
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
  className?: string;
}

function ariaSort(sort: Sort | null | undefined, id: string): "ascending" | "descending" | "none" {
  if (sort?.id !== id) return "none";
  return sort.desc ? "descending" : "ascending";
}

export function DataTable<T extends object>({
  caption,
  columns,
  rows,
  getRowId,
  rowTestId,
  sort = null,
  onSortChange,
  loading = false,
  error = null,
  emptyMessage = "Nothing to show.",
  toolbar,
  hasMore = false,
  loadingMore = false,
  onLoadMore,
  className,
}: DataTableProps<T>) {
  const menuId = useId();
  const [menuOpen, setMenuOpen] = useState(false);
  const [sortingState, setSortingState] = useState<SortingState>([]);

  const definitions = useMemo<ColumnDef<typeof features, T>[]>(
    () =>
      columns.map((column) => ({
        id: column.id,
        header: column.header,
        accessorFn: (row: T) => row,
        cell: ({ row }) => column.cell(row.original),
        enableSorting: Boolean(column.sortable && onSortChange),
        enableHiding: column.hideable ?? true,
      })),
    [columns, onSortChange],
  );
  const initialVisibility = useMemo(
    () => Object.fromEntries(columns.filter((c) => c.defaultHidden).map((c) => [c.id, false])),
    [columns],
  );

  const table = useTable<typeof features, T>({
    features,
    columns: definitions,
    data: rows as T[],
    getRowId,
    manualSorting: true,
    initialState: { columnVisibility: initialVisibility },
    state: { sorting: sort ? [{ id: sort.id, desc: sort.desc }] : sortingState },
    onSortingChange: (updater) => {
      const current: SortingState = sort ? [{ id: sort.id, desc: sort.desc }] : sortingState;
      const next = typeof updater === "function" ? updater(current) : updater;
      setSortingState(next);
      const first = next[0];
      onSortChange?.(first ? { id: first.id, desc: first.desc } : null);
    },
  });

  const visible = table.getVisibleLeafColumns();
  const hideable = table.getAllLeafColumns().filter((column) => column.getCanHide());

  function cycle(id: string) {
    if (!onSortChange) return;
    if (sort?.id !== id) onSortChange({ id, desc: false });
    else if (!sort.desc) onSortChange({ id, desc: true });
    else onSortChange(null);
  }

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      {toolbar || hideable.length > 1 ? (
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-wrap items-end gap-3">{toolbar}</div>
          {hideable.length > 1 ? (
            <div className="relative">
              <Button
                variant="outline"
                size="sm"
                aria-expanded={menuOpen}
                aria-controls={menuId}
                onClick={() => setMenuOpen((open) => !open)}
              >
                Columns
              </Button>
              {menuOpen ? (
                <fieldset
                  id={menuId}
                  className="absolute right-0 z-10 mt-2 min-w-44 rounded-md border border-border bg-card p-3 text-sm shadow-lg"
                  onKeyDown={(event) => event.key === "Escape" && setMenuOpen(false)}
                >
                  <legend className="sr-only">Visible columns</legend>
                  {hideable.map((column) => (
                    <label key={column.id} className="flex items-center gap-2 py-1">
                      <input
                        type="checkbox"
                        className="size-4 accent-primary"
                        checked={column.getIsVisible()}
                        onChange={column.getToggleVisibilityHandler()}
                      />
                      {typeof column.columnDef.header === "string"
                        ? column.columnDef.header
                        : column.id}
                    </label>
                  ))}
                </fieldset>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
      {error ? <Alert variant="destructive">{error}</Alert> : null}
      <div className="overflow-x-auto rounded-lg border border-border bg-card">
        <table className="w-full text-left text-sm" aria-busy={loading || undefined}>
          <caption className="sr-only">{caption}</caption>
          <thead className="border-b border-border bg-muted/50">
            <tr>
              {visible.map((column) => {
                const source = columns.find((c) => c.id === column.id);
                const sortable = column.getCanSort();
                return (
                  <th
                    key={column.id}
                    scope="col"
                    aria-sort={sortable ? ariaSort(sort, column.id) : undefined}
                    className={cn("px-3 py-2 font-medium", source?.className)}
                  >
                    {sortable ? (
                      <button
                        type="button"
                        onClick={() => cycle(column.id)}
                        className="inline-flex items-center gap-1 rounded focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                      >
                        {source?.header}
                        <span aria-hidden="true" className="text-xs text-muted-foreground">
                          {sort?.id === column.id ? (sort.desc ? "▼" : "▲") : "↕"}
                        </span>
                      </button>
                    ) : (
                      source?.header
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                data-testid={rowTestId ? rowTestId(row.original) : undefined}
                className="hover:bg-muted/40"
              >
                {visible.map((column) => {
                  const source = columns.find((c) => c.id === column.id);
                  return (
                    <td key={column.id} className={cn("px-3 py-2 align-top", source?.className)}>
                      {source?.cell(row.original)}
                    </td>
                  );
                })}
              </tr>
            ))}
            {loading && rows.length === 0 ? (
              <tr>
                <td
                  colSpan={visible.length}
                  className="px-3 py-6 text-center text-muted-foreground"
                >
                  Loading…
                </td>
              </tr>
            ) : null}
            {!loading && !error && rows.length === 0 ? (
              <tr>
                <td
                  colSpan={visible.length}
                  className="px-3 py-6 text-center text-muted-foreground"
                  data-testid="table-empty"
                >
                  {emptyMessage}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {hasMore ? (
        <div className="text-center">
          <Button variant="outline" disabled={loadingMore} onClick={onLoadMore}>
            {loadingMore ? "Loading…" : "Load more"}
          </Button>
        </div>
      ) : null}
      <p className="sr-only" role="status">
        {loading ? "Loading" : `${rows.length} rows`}
      </p>
    </div>
  );
}
