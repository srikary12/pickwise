// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { EmployeeOut, JobRecordOut } from "@pickwise/api-client";
import {
  Alert,
  Button,
  DateField,
  dateSchema,
  Form,
  SelectField,
  TextField,
  useZodForm,
} from "@pickwise/ui";
import { useRouter } from "next/navigation";
import { z } from "zod";

import { NoAccess } from "@/components/no-access";
import { api, call, errorMessage } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { EMPLOYMENT_TYPES, useOrgOptions } from "@/lib/org-options";
import { can, useSession } from "@/lib/session";

const schema = z.object({
  first_name: z.string().trim().min(1, "Enter the first name.").max(100),
  last_name: z.string().trim().max(100),
  work_email: z
    .string()
    .trim()
    .refine(
      (v) => v === "" || /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v),
      "Enter a valid email address.",
    ),
  date_of_joining: dateSchema("Choose the joining date."),
  legal_entity_id: z.string().min(1, "Choose a legal entity."),
  location_id: z.string().min(1, "Choose a location."),
  department_id: z.string().min(1, "Choose a department."),
  designation_id: z.string().min(1, "Choose a designation."),
  grade_id: z.string(),
  manager_employee_id: z.string(),
  employment_type: z.string().min(1),
});
type Values = z.infer<typeof schema>;

export default function NewEmployeePage() {
  const router = useRouter();
  const { data: session } = useSession();
  const options = useOrgOptions();
  const form = useZodForm(schema, {
    first_name: "",
    last_name: "",
    work_email: "",
    date_of_joining: "",
    legal_entity_id: "",
    location_id: "",
    department_id: "",
    designation_id: "",
    grade_id: "",
    manager_employee_id: "",
    employment_type: "full_time",
  });
  const create = useApiMutation({
    mutationFn: (v: Values, { idempotencyKey }) =>
      call<EmployeeOut>(
        api().POST("/v1/employees", {
          headers: { "Idempotency-Key": idempotencyKey },
          body: {
            first_name: v.first_name,
            last_name: v.last_name || null,
            work_email: v.work_email || null,
            date_of_joining: v.date_of_joining,
            custom_fields: {},
            job: {
              legal_entity_id: v.legal_entity_id,
              location_id: v.location_id,
              department_id: v.department_id,
              designation_id: v.designation_id,
              grade_id: v.grade_id || null,
              manager_employee_id: v.manager_employee_id || null,
              employment_type: v.employment_type as JobRecordOut["employment_type"],
            },
          },
        }),
      ),
    onSuccess: (employee) => router.push(`/employees/${employee.id}`),
  });

  if (!session) return null;
  if (!can(session, "core.employees.create")) return <NoAccess />;
  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <h1 className="text-2xl font-semibold">Add employee</h1>
      <Form
        form={form}
        label="Add employee"
        onSubmit={async (values) => {
          await create.mutateAsync(values).catch(() => undefined);
        }}
      >
        <fieldset className="grid gap-4 sm:grid-cols-2">
          <legend className="mb-2 font-medium">Who</legend>
          <TextField form={form} name="first_name" label="First name" />
          <TextField form={form} name="last_name" label="Last name" />
          <TextField form={form} name="work_email" label="Work email" type="email" />
          <DateField form={form} name="date_of_joining" label="Joining date" />
        </fieldset>
        <fieldset className="grid gap-4 sm:grid-cols-2">
          <legend className="mb-2 font-medium">Job</legend>
          <SelectField
            form={form}
            name="legal_entity_id"
            label="Legal entity"
            placeholder="Choose…"
            options={options.entities}
          />
          <SelectField
            form={form}
            name="location_id"
            label="Location"
            placeholder="Choose…"
            options={options.locations}
          />
          <SelectField
            form={form}
            name="department_id"
            label="Department"
            placeholder="Choose…"
            options={options.departments}
          />
          <SelectField
            form={form}
            name="designation_id"
            label="Designation"
            placeholder="Choose…"
            options={options.designations}
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
            name="manager_employee_id"
            label="Manager"
            placeholder="No manager"
            options={options.managers}
          />
          <SelectField
            form={form}
            name="employment_type"
            label="Employment type"
            options={EMPLOYMENT_TYPES}
          />
        </fieldset>
        <p className="text-sm text-muted-foreground">
          They start as a draft with an employee code from your sequence. Start onboarding or make
          them active from their profile.
        </p>
        {create.error ? <Alert variant="destructive">{errorMessage(create.error)}</Alert> : null}
        <div className="flex gap-3">
          <Button type="submit" disabled={create.isPending}>
            Create employee
          </Button>
          <Button variant="outline" onClick={() => router.push("/employees")}>
            Cancel
          </Button>
        </div>
      </Form>
    </div>
  );
}
