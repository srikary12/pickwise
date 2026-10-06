// SPDX-License-Identifier: AGPL-3.0-only
"use client";

// One screen for every simple org list (entities, locations, cost centres, designations,
// grades): a table with an "include archived" switch, and a dialog to add or edit. What differs
// between lists is described by a `Resource`; everything shared (conflict prompt, idempotent
// creates, permission-gated buttons) lives here.
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  DataTable,
  type DataTableColumn,
  Dialog,
  Form,
  Label,
  useZodForm,
} from "@pickwise/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ComponentType, type ReactNode, useId, useState } from "react";
import type { FieldValues, UseFormReturn } from "react-hook-form";
import type { z } from "zod";

import { errorMessage } from "@/lib/api";
import { showConflict } from "@/lib/conflict";
import { useApiMutation } from "@/lib/mutations";
import { isStaleVersion } from "@/lib/query";

export interface OrgRow {
  id: string;
  archived_at?: string | null;
  row_version: number;
}

export interface Resource<Row extends OrgRow, V extends FieldValues> {
  /** Query-key part and test-id prefix. */
  id: string;
  /** "Locations" */
  title: string;
  /** "location" */
  singular: string;
  schema: z.ZodType<V, V>;
  defaults: V;
  toForm: (row: Row) => V;
  Fields: ComponentType<{ form: UseFormReturn<V, unknown, V> }>;
  columns: DataTableColumn<Row>[];
  list: (includeArchived: boolean) => Promise<Row[]>;
  create: (values: V, idempotencyKey: string) => Promise<unknown>;
  update: (row: Row, values: V) => Promise<unknown>;
  setArchived: (row: Row, archived: boolean) => Promise<unknown>;
  /** Extra per-row buttons (e.g. "Registrations"). */
  rowActions?: (row: Row, canManage: boolean) => ReactNode;
  /** Which other lists this one's form selects from, so they refresh together. */
  rowLabel: (row: Row) => string;
}

function EditDialog<Row extends OrgRow, V extends FieldValues>({
  resource,
  row,
  onClose,
}: {
  resource: Resource<Row, V>;
  /** Null to add a new one. */
  row: Row | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const form = useZodForm(
    resource.schema,
    (row ? resource.toForm(row) : resource.defaults) as never,
  );
  const save = useApiMutation({
    mutationFn: (values: V, { idempotencyKey }) =>
      row ? resource.update(row, values) : resource.create(values, idempotencyKey),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["org"] });
      onClose();
    },
    onError: (error) => {
      if (isStaleVersion(error)) showConflict();
    },
  });
  const title = `${row ? "Edit" : "Add"} ${resource.singular}`;
  return (
    <Form
      form={form as never}
      label={title}
      onSubmit={async (values) => {
        await save.mutateAsync(values as V).catch(() => undefined); // shown below
      }}
    >
      <resource.Fields form={form as unknown as UseFormReturn<V, unknown, V>} />
      {save.error ? (
        <Alert variant="destructive" data-testid="org-form-error">
          {errorMessage(save.error)}
        </Alert>
      ) : null}
      <div className="flex justify-end gap-3">
        <Button variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" disabled={save.isPending}>
          Save
        </Button>
      </div>
    </Form>
  );
}

export function ResourcePanel<Row extends OrgRow, V extends FieldValues>({
  resource,
  canManage,
}: {
  resource: Resource<Row, V>;
  canManage: boolean;
}) {
  const queryClient = useQueryClient();
  const switchId = useId();
  const [archived, setArchived] = useState(false);
  const [editing, setEditing] = useState<Row | "new" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const rows = useQuery({
    queryKey: ["org", resource.id, { archived }],
    queryFn: () => resource.list(archived),
  });
  const toggle = useMutation({
    mutationFn: ({ row, archive }: { row: Row; archive: boolean }) =>
      resource.setArchived(row, archive),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["org"] }),
    onError: (error) => {
      if (isStaleVersion(error)) showConflict();
      else setNotice(errorMessage(error));
    },
  });

  const columns: DataTableColumn<Row>[] = [
    ...resource.columns,
    ...(canManage || resource.rowActions
      ? [
          {
            id: "actions",
            header: "Actions",
            hideable: false,
            className: "text-right",
            cell: (row: Row) => (
              <div className="flex justify-end gap-2">
                {resource.rowActions?.(row, canManage)}
                {canManage ? (
                  <>
                    <Button
                      size="sm"
                      variant="outline"
                      aria-label={`Edit ${resource.rowLabel(row)}`}
                      onClick={() => setEditing(row)}
                    >
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      aria-label={`${row.archived_at ? "Restore" : "Archive"} ${resource.rowLabel(row)}`}
                      disabled={toggle.isPending}
                      onClick={() => {
                        setNotice(null);
                        toggle.mutate({ row, archive: !row.archived_at });
                      }}
                    >
                      {row.archived_at ? "Restore" : "Archive"}
                    </Button>
                  </>
                ) : null}
              </div>
            ),
          } satisfies DataTableColumn<Row>,
        ]
      : []),
  ];

  return (
    <div className="flex flex-col gap-3" data-testid={`org-${resource.id}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Checkbox
            id={switchId}
            checked={archived}
            onChange={(event) => setArchived(event.target.checked)}
          />
          <Label htmlFor={switchId} className="font-normal">
            Show archived
          </Label>
        </div>
        {canManage ? (
          <Button onClick={() => setEditing("new")}>Add {resource.singular}</Button>
        ) : null}
      </div>
      {notice ? <Alert variant="destructive">{notice}</Alert> : null}
      <DataTable
        caption={resource.title}
        columns={columns}
        rows={rows.data ?? []}
        getRowId={(row) => row.id}
        rowTestId={(row) => `${resource.id}-${resource.rowLabel(row)}`}
        loading={rows.isLoading}
        error={rows.isError ? errorMessage(rows.error) : null}
        emptyMessage={`No ${resource.title.toLowerCase()} yet.`}
      />
      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={`${editing === "new" ? "Add" : "Edit"} ${resource.singular}`}
        className="max-w-xl"
      >
        {editing !== null ? (
          <EditDialog
            key={editing === "new" ? "new" : editing.id}
            resource={resource}
            row={editing === "new" ? null : editing}
            onClose={() => setEditing(null)}
          />
        ) : null}
      </Dialog>
    </div>
  );
}

/** "Archived" next to a name in a table cell. */
export function ArchivedBadge({ row }: { row: OrgRow }) {
  return row.archived_at ? (
    <Badge variant="outline" className="ml-2">
      Archived
    </Badge>
  ) : null;
}
