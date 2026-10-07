// SPDX-License-Identifier: AGPL-3.0-only
"use client";

// The job history as a timeline, and the "change job" dialog: a promotion or transfer closes the
// current record and opens a new one from a date (the past, today or the future). Nothing is
// overwritten.
import type { EmployeeOut, JobRecordOut } from "@pickwise/api-client";
import {
  Alert,
  Button,
  DateField,
  dateSchema,
  Dialog,
  EffectiveDatedTimeline,
  Form,
  SelectField,
  TextField,
  useZodForm,
} from "@pickwise/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { z } from "zod";

import { api, call, errorMessage } from "@/lib/api";
import { showConflict } from "@/lib/conflict";
import { useApiMutation } from "@/lib/mutations";
import { EMPLOYMENT_TYPES, useOrgOptions } from "@/lib/org-options";
import { isStaleVersion } from "@/lib/query";

const REASONS = [
  { value: "promotion", label: "Promotion" },
  { value: "transfer", label: "Transfer" },
  { value: "redesignation", label: "Redesignation" },
  { value: "manager_change", label: "Manager change" },
] as const;

const schema = z.object({
  effective_from: dateSchema("Choose the date it takes effect."),
  reason: z.enum(["promotion", "transfer", "redesignation", "manager_change"]),
  location_id: z.string().min(1),
  department_id: z.string().min(1),
  designation_id: z.string().min(1),
  grade_id: z.string(),
  cost_center_id: z.string(),
  manager_employee_id: z.string(),
  employment_type: z.string().min(1),
  notes: z.string().max(1000),
});
type Values = z.infer<typeof schema>;

function ChangeDialog({
  employee,
  current,
  open,
  onClose,
}: {
  employee: EmployeeOut;
  current: JobRecordOut | undefined;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const options = useOrgOptions();
  const form = useZodForm(schema, {
    effective_from: "",
    reason: "promotion",
    location_id: current?.location_id ?? "",
    department_id: current?.department_id ?? "",
    designation_id: current?.designation_id ?? "",
    grade_id: current?.grade_id ?? "",
    cost_center_id: current?.cost_center_id ?? "",
    manager_employee_id: current?.manager_employee_id ?? "",
    employment_type: current?.employment_type ?? "full_time",
    notes: "",
  });
  const save = useApiMutation({
    mutationFn: (v: Values, { idempotencyKey }) =>
      call(
        api().POST("/v1/employees/{employee_id}/job-records", {
          params: { path: { employee_id: employee.id } },
          headers: { "Idempotency-Key": idempotencyKey },
          body: {
            effective_from: v.effective_from,
            reason: v.reason,
            notes: v.notes || null,
            location_id: v.location_id,
            department_id: v.department_id,
            designation_id: v.designation_id,
            grade_id: v.grade_id || null,
            cost_center_id: v.cost_center_id || null,
            manager_employee_id: v.manager_employee_id || null,
            employment_type: v.employment_type as JobRecordOut["employment_type"],
          },
        }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["employee", employee.id] });
      onClose();
    },
    onError: (error) => {
      if (isStaleVersion(error)) showConflict();
    },
  });
  return (
    <Dialog open={open} onClose={onClose} title="Change job" className="max-w-2xl">
      <Form
        form={form}
        label="Change job"
        onSubmit={async (values) => {
          await save.mutateAsync(values).catch(() => undefined);
        }}
      >
        <p className="text-sm text-muted-foreground">
          The record in force on that date ends the day before. A future date schedules the change.
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <DateField form={form} name="effective_from" label="Takes effect on" />
          <SelectField form={form} name="reason" label="Reason" options={REASONS} />
          <SelectField
            form={form}
            name="designation_id"
            label="Designation"
            options={options.designations}
          />
          <SelectField
            form={form}
            name="department_id"
            label="Department"
            options={options.departments}
          />
          <SelectField
            form={form}
            name="location_id"
            label="Location"
            options={options.locations}
          />
          <SelectField
            form={form}
            name="grade_id"
            label="Grade"
            placeholder="None"
            options={options.grades}
          />
          <SelectField
            form={form}
            name="cost_center_id"
            label="Cost centre"
            placeholder="None"
            options={options.costCenters}
          />
          <SelectField
            form={form}
            name="manager_employee_id"
            label="Manager"
            placeholder="No manager"
            options={options.managers.filter((m) => m.value !== employee.id)}
          />
          <SelectField
            form={form}
            name="employment_type"
            label="Employment type"
            options={EMPLOYMENT_TYPES}
          />
          <TextField form={form} name="notes" label="Notes" />
        </div>
        {save.error ? <Alert variant="destructive">{errorMessage(save.error)}</Alert> : null}
        <div className="flex justify-end gap-3">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={save.isPending}>
            Save change
          </Button>
        </div>
      </Form>
    </Dialog>
  );
}

export function JobTab({
  employee,
  canChange,
  self = false,
}: {
  employee: EmployeeOut;
  canChange: boolean;
  self?: boolean;
}) {
  const [changing, setChanging] = useState(false);
  const records = useQuery({
    queryKey: ["employee", employee.id, "job-records"],
    queryFn: () =>
      call<JobRecordOut[]>(
        self
          ? api().GET("/v1/me/employee/job-records")
          : api().GET("/v1/employees/{employee_id}/job-records", {
              params: { path: { employee_id: employee.id } },
            }),
      ),
  });
  const options = useOrgOptions();
  const label = (list: { value: string; label: string }[], id: string | null | undefined) =>
    list.find((o) => o.value === id)?.label ?? "—";
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  const items = (records.data ?? []).map((r) => ({
    id: r.id,
    validFrom: r.valid_from,
    validTo: r.valid_to ?? null,
    title: `${label(options.designations, r.designation_id)} · ${label(options.departments, r.department_id).replace(/^(— )+/, "")}`,
    detail: (
      <>
        {label(options.locations, r.location_id)} · {r.employment_type.replace("_", " ")} · reports
        to {r.manager_employee_id ? label(options.managers, r.manager_employee_id) : "no one"}
      </>
    ),
  }));
  const current = records.data?.find((r) => r.is_current);
  return (
    <div className="flex flex-col gap-4" data-testid="job-tab">
      {canChange ? (
        <div className="flex justify-end">
          <Button onClick={() => setChanging(true)}>Change job</Button>
        </div>
      ) : null}
      {records.isError ? <Alert variant="destructive">{errorMessage(records.error)}</Alert> : null}
      <EffectiveDatedTimeline items={items} today={today} label="Job history" />
      {canChange ? (
        <ChangeDialog
          key={current?.id ?? "none"}
          employee={employee}
          current={current}
          open={changing}
          onClose={() => setChanging(false)}
        />
      ) : null}
    </div>
  );
}
