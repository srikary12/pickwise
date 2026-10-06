// SPDX-License-Identifier: AGPL-3.0-only
"use client";

// The department tree. A department moves with its whole subtree, two ways: drag it onto its new
// parent (or onto "Top level"), or press "Move to…" and pick the parent from a list. The second is
// the keyboard and screen-reader path, and it lists only valid targets (not the department itself
// or anything below it); the server still refuses a cycle.
import type { CostCenterOut, DepartmentOut } from "@pickwise/api-client";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Dialog,
  Form,
  Label,
  Select,
  SelectField,
  TextField,
  useZodForm,
} from "@pickwise/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";
import { z } from "zod";

import { api, call, errorMessage } from "@/lib/api";
import { showConflict } from "@/lib/conflict";
import { useApiMutation } from "@/lib/mutations";
import { isStaleVersion } from "@/lib/query";

const schema = z.object({
  code: z
    .string()
    .trim()
    .regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,29}$/, "Use letters, digits, - or _ (up to 30)."),
  name: z.string().trim().min(1, "Enter a name.").max(200),
  cost_center_id: z.string(),
});
type Values = z.infer<typeof schema>;

type Editing = { kind: "add"; parent: DepartmentOut | null } | { kind: "edit"; row: DepartmentOut };

function DepartmentForm({
  editing,
  costCenters,
  onClose,
}: {
  editing: Editing;
  costCenters: CostCenterOut[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const row = editing.kind === "edit" ? editing.row : null;
  const form = useZodForm(schema, {
    code: row?.code ?? "",
    name: row?.name ?? "",
    cost_center_id: row?.cost_center_id ?? "",
  });
  const save = useApiMutation({
    mutationFn: (values: Values, { idempotencyKey }) => {
      const body = { ...values, cost_center_id: values.cost_center_id || null };
      return row
        ? call(
            api().PUT("/v1/org/departments/{department_id}", {
              params: { path: { department_id: row.id } },
              body: { ...body, row_version: row.row_version },
            }),
          )
        : call(
            api().POST("/v1/org/departments", {
              body: {
                ...body,
                parent_id: editing.kind === "add" ? (editing.parent?.id ?? null) : null,
              },
              headers: { "Idempotency-Key": idempotencyKey },
            }),
          );
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["org"] });
      onClose();
    },
    onError: (error) => {
      if (isStaleVersion(error)) showConflict();
    },
  });
  return (
    <Form
      form={form}
      label={row ? "Edit department" : "Add department"}
      onSubmit={async (values) => {
        await save.mutateAsync(values).catch(() => undefined);
      }}
    >
      {editing.kind === "add" ? (
        <p className="text-sm text-muted-foreground">
          {editing.parent ? `Inside ${editing.parent.name}.` : "At the top level."}
        </p>
      ) : null}
      <TextField form={form} name="code" label="Code" />
      <TextField form={form} name="name" label="Name" />
      <SelectField
        form={form}
        name="cost_center_id"
        label="Cost centre"
        placeholder="None"
        options={costCenters.map((c) => ({ value: c.id, label: `${c.code} · ${c.name}` }))}
      />
      {save.error ? <Alert variant="destructive">{errorMessage(save.error)}</Alert> : null}
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

function MoveDialog({
  department,
  all,
  onMove,
  onClose,
}: {
  department: DepartmentOut | null;
  all: DepartmentOut[];
  onMove: (department: DepartmentOut, parentId: string | null) => Promise<void>;
  onClose: () => void;
}) {
  const [target, setTarget] = useState("");
  const id = useId();
  const options = department
    ? all.filter(
        (d) =>
          !d.archived_at && d.id !== department.id && !d.path.startsWith(`${department.path}.`),
      )
    : [];
  return (
    <Dialog
      open={department !== null}
      onClose={onClose}
      title={department ? `Move ${department.name}` : "Move"}
    >
      {department ? (
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            void onMove(department, target || null).then(onClose);
          }}
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={id}>New parent</Label>
            <Select id={id} value={target} onChange={(event) => setTarget(event.target.value)}>
              <option value="">Top level</option>
              {options.map((d) => (
                <option key={d.id} value={d.id}>
                  {"— ".repeat(d.depth)}
                  {d.name}
                </option>
              ))}
            </Select>
          </div>
          <div className="flex justify-end gap-3">
            <Button variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit">Move</Button>
          </div>
        </form>
      ) : null}
    </Dialog>
  );
}

export function DepartmentTree({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient();
  const switchId = useId();
  const [archived, setArchived] = useState(false);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [moving, setMoving] = useState<DepartmentOut | null>(null);
  const [dragging, setDragging] = useState<DepartmentOut | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const departments = useQuery({
    queryKey: ["org", "departments", { archived }],
    queryFn: () =>
      call<DepartmentOut[]>(
        api().GET("/v1/org/departments", { params: { query: { include_archived: archived } } }),
      ),
  });
  const costCenters = useQuery({
    queryKey: ["org", "cost-centers", { archived: false }],
    queryFn: () =>
      call<CostCenterOut[]>(api().GET("/v1/org/cost-centers", { params: { query: {} } })),
  });

  const rows = useMemo(() => departments.data ?? [], [departments.data]);
  const children = useMemo(() => {
    const map = new Map<string | null, DepartmentOut[]>();
    for (const row of rows) {
      const list = map.get(row.parent_id ?? null) ?? [];
      list.push(row);
      map.set(row.parent_id ?? null, list);
    }
    return map;
  }, [rows]);

  async function run(action: () => Promise<unknown>) {
    setNotice(null);
    try {
      await action();
    } catch (error) {
      if (isStaleVersion(error)) showConflict();
      else setNotice(errorMessage(error));
    }
    await queryClient.invalidateQueries({ queryKey: ["org"] });
  }

  const move = (department: DepartmentOut, parentId: string | null) =>
    run(() =>
      call(
        api().POST("/v1/org/departments/{department_id}/move", {
          params: { path: { department_id: department.id } },
          body: { parent_id: parentId, row_version: department.row_version },
        }),
      ),
    );

  const setArchivedState = (department: DepartmentOut, archive: boolean) =>
    run(() =>
      call(
        api().POST(
          archive
            ? "/v1/org/departments/{department_id}/archive"
            : "/v1/org/departments/{department_id}/unarchive",
          { params: { path: { department_id: department.id } } },
        ),
      ),
    );

  function dropOn(parent: DepartmentOut | null) {
    const item = dragging;
    setDragging(null);
    setOver(null);
    if (!item || (parent && (parent.id === item.id || parent.path.startsWith(`${item.path}.`)))) {
      return;
    }
    if ((item.parent_id ?? null) === (parent?.id ?? null)) return;
    void move(item, parent?.id ?? null);
  }

  function dropProps(parent: DepartmentOut | null) {
    const key = parent?.id ?? "top";
    return {
      onDragOver: (event: React.DragEvent) => {
        if (!dragging) return;
        event.preventDefault();
        setOver(key);
      },
      onDragLeave: () => setOver((current) => (current === key ? null : current)),
      onDrop: (event: React.DragEvent) => {
        event.preventDefault();
        dropOn(parent);
      },
    };
  }

  // A plain function, not a component: a component declared here would be a new type on every
  // render, remounting the rows mid-drag and cancelling the drag.
  function renderNode(department: DepartmentOut) {
    const kids = children.get(department.id) ?? [];
    return (
      <li key={department.id}>
        <div
          data-testid={`department-${department.code}`}
          draggable={canManage && !department.archived_at}
          onDragStart={() => setDragging(department)}
          onDragEnd={() => {
            setDragging(null);
            setOver(null);
          }}
          {...(canManage ? dropProps(department) : {})}
          className={`flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2 ${
            over === department.id ? "border-primary bg-muted" : "border-border bg-card"
          } ${canManage && !department.archived_at ? "cursor-grab" : ""}`}
        >
          <div>
            <span className="font-medium">{department.name}</span>
            <span className="ml-2 text-xs text-muted-foreground">{department.code}</span>
            {department.archived_at ? (
              <Badge variant="outline" className="ml-2">
                Archived
              </Badge>
            ) : null}
          </div>
          {canManage ? (
            <div className="flex flex-wrap gap-2">
              {department.archived_at ? null : (
                <>
                  <Button
                    size="sm"
                    variant="outline"
                    aria-label={`Add a department inside ${department.name}`}
                    onClick={() => setEditing({ kind: "add", parent: department })}
                  >
                    Add inside
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    aria-label={`Edit ${department.name}`}
                    onClick={() => setEditing({ kind: "edit", row: department })}
                  >
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    aria-label={`Move ${department.name}`}
                    onClick={() => setMoving(department)}
                  >
                    Move to…
                  </Button>
                </>
              )}
              <Button
                size="sm"
                variant="outline"
                aria-label={`${department.archived_at ? "Restore" : "Archive"} ${department.name}`}
                onClick={() => void setArchivedState(department, !department.archived_at)}
              >
                {department.archived_at ? "Restore" : "Archive"}
              </Button>
            </div>
          ) : null}
        </div>
        {kids.length > 0 ? (
          <ul className="ml-6 mt-2 flex flex-col gap-2 border-l border-border pl-3">
            {kids.map(renderNode)}
          </ul>
        ) : null}
      </li>
    );
  }

  const top = children.get(null) ?? [];
  return (
    <div className="flex flex-col gap-3" data-testid="org-departments">
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
          <Button onClick={() => setEditing({ kind: "add", parent: null })}>Add department</Button>
        ) : null}
      </div>
      {canManage ? (
        <p className="text-sm text-muted-foreground">
          Drag a department onto another to move it with everything inside it, or use “Move to…”.
        </p>
      ) : null}
      {notice ? <Alert variant="destructive">{notice}</Alert> : null}
      {departments.isError ? (
        <Alert variant="destructive">{errorMessage(departments.error)}</Alert>
      ) : null}
      {canManage ? (
        <div
          {...dropProps(null)}
          className={`rounded-md border border-dashed px-3 py-2 text-center text-sm ${
            over === "top" ? "border-primary bg-muted" : "border-border text-muted-foreground"
          }`}
        >
          Drop here to move to the top level
        </div>
      ) : null}
      {departments.isLoading ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading…
        </p>
      ) : top.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="table-empty">
          No departments yet.
        </p>
      ) : (
        <ul aria-label="Departments" className="flex flex-col gap-2">
          {top.map(renderNode)}
        </ul>
      )}
      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing?.kind === "edit" ? "Edit department" : "Add department"}
      >
        {editing ? (
          <DepartmentForm
            key={editing.kind === "edit" ? editing.row.id : (editing.parent?.id ?? "top")}
            editing={editing}
            costCenters={costCenters.data ?? []}
            onClose={() => setEditing(null)}
          />
        ) : null}
      </Dialog>
      <MoveDialog department={moving} all={rows} onMove={move} onClose={() => setMoving(null)} />
    </div>
  );
}
