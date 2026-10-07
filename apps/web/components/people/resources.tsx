// SPDX-License-Identifier: AGPL-3.0-only
"use client";

// The lists on an employee's profile that are plain "add, edit, delete" rows. `self` switches the
// calls to the person's own record (/v1/me/employee), which only offers contacts.
import { SelectField, TextField } from "@pickwise/ui";
import { z } from "zod";

import type { Resource } from "@/components/org/resource-panel";
import { api, call } from "@/lib/api";

const required = (label: string, max = 200) =>
  z.string().trim().min(1, `Enter ${label}.`).max(max, `Use at most ${max} characters.`);
const optionalText = (max: number) => z.string().trim().max(max, `Use at most ${max} characters.`);
const optional = (pattern: RegExp, message: string) =>
  z
    .string()
    .trim()
    .refine((value) => value === "" || pattern.test(value), message);
const orNull = (value: string): string | null => (value === "" ? null : value);
const year = optional(/^(19[5-9]\d|20\d\d|2100)$/, "Enter a year like 2014.");
const date = optional(/^\d{4}-\d{2}-\d{2}$/, "Enter a valid date.");

// --- emergency contacts -------------------------------------------------------------------------

const contactSchema = z.object({
  name: required("a name"),
  relationship: required("how they're related", 60),
  phone: z
    .string()
    .trim()
    .regex(/^[0-9+()\-\s]{5,30}$/, "Enter a phone number."),
  email: optional(/^[^@\s]+@[^@\s]+\.[^@\s]+$/, "Enter a valid email address."),
  is_primary: z.boolean(),
});
type ContactValues = z.infer<typeof contactSchema>;

interface ContactRow {
  id: string;
  name: string;
  relationship: string;
  phone: string;
  email?: string | null;
  is_primary: boolean;
  row_version: number;
}

export function emergencyContacts(
  employeeId: string,
  self: boolean,
): Resource<ContactRow, ContactValues> {
  const body = (v: ContactValues) => ({ ...v, email: orNull(v.email) });
  return {
    id: `contacts-${employeeId}`,
    title: "Emergency contacts",
    singular: "contact",
    schema: contactSchema,
    defaults: { name: "", relationship: "", phone: "", email: "", is_primary: false },
    toForm: (row) => ({
      name: row.name,
      relationship: row.relationship,
      phone: row.phone,
      email: row.email ?? "",
      is_primary: row.is_primary,
    }),
    Fields: ({ form }) => (
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField form={form} name="name" label="Name" />
        <TextField form={form} name="relationship" label="Relationship" />
        <TextField form={form} name="phone" label="Phone" type="tel" />
        <TextField form={form} name="email" label="Email" type="email" />
        <label className="flex items-center gap-2 text-sm sm:col-span-2">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            {...form.register("is_primary")}
          />
          The first person to call
        </label>
      </div>
    ),
    columns: [
      {
        id: "name",
        header: "Name",
        hideable: false,
        cell: (row) => (
          <>
            <span className="font-medium">{row.name}</span>
            {row.is_primary ? (
              <span className="ml-2 text-xs text-muted-foreground">Primary</span>
            ) : null}
          </>
        ),
      },
      { id: "relationship", header: "Relationship", cell: (row) => row.relationship },
      { id: "phone", header: "Phone", cell: (row) => row.phone },
    ],
    list: () =>
      call<ContactRow[]>(
        self
          ? api().GET("/v1/me/employee/emergency-contacts")
          : api().GET("/v1/employees/{employee_id}/emergency-contacts", {
              params: { path: { employee_id: employeeId } },
            }),
      ),
    create: (v, key) =>
      call(
        self
          ? api().POST("/v1/me/employee/emergency-contacts", {
              body: body(v),
              headers: { "Idempotency-Key": key },
            })
          : api().POST("/v1/employees/{employee_id}/emergency-contacts", {
              params: { path: { employee_id: employeeId } },
              body: body(v),
              headers: { "Idempotency-Key": key },
            }),
      ),
    update: (row, v) =>
      call(
        self
          ? api().PUT("/v1/me/employee/emergency-contacts/{contact_id}", {
              params: { path: { contact_id: row.id } },
              body: { ...body(v), row_version: row.row_version },
            })
          : api().PUT("/v1/employees/{employee_id}/emergency-contacts/{contact_id}", {
              params: { path: { employee_id: employeeId, contact_id: row.id } },
              body: { ...body(v), row_version: row.row_version },
            }),
      ),
    remove: (row) =>
      call(
        self
          ? api().DELETE("/v1/me/employee/emergency-contacts/{contact_id}", {
              params: { path: { contact_id: row.id } },
            })
          : api().DELETE("/v1/employees/{employee_id}/emergency-contacts/{contact_id}", {
              params: { path: { employee_id: employeeId, contact_id: row.id } },
            }),
      ),
    rowLabel: (row) => row.name,
  };
}

// --- dependents ---------------------------------------------------------------------------------

const RELATIONSHIPS = [
  { value: "spouse", label: "Spouse" },
  { value: "child", label: "Child" },
  { value: "father", label: "Father" },
  { value: "mother", label: "Mother" },
  { value: "sibling", label: "Sibling" },
  { value: "other", label: "Other" },
] as const;
const GENDERS = [
  { value: "female", label: "Female" },
  { value: "male", label: "Male" },
  { value: "non_binary", label: "Non-binary" },
  { value: "undisclosed", label: "Prefer not to say" },
] as const;

const dependentSchema = z.object({
  name: required("a name"),
  relationship: z.enum(["spouse", "child", "father", "mother", "sibling", "other"]),
  date_of_birth: date,
  gender: z.string(),
});
type DependentValues = z.infer<typeof dependentSchema>;

export interface DependentRow {
  id: string;
  name: string;
  relationship: DependentValues["relationship"];
  date_of_birth?: string | null;
  gender?: string | null;
  row_version: number;
}

export function dependents(employeeId: string): Resource<DependentRow, DependentValues> {
  const body = (v: DependentValues) => ({
    name: v.name,
    relationship: v.relationship,
    date_of_birth: orNull(v.date_of_birth),
    gender: (v.gender || null) as "female" | "male" | "non_binary" | "undisclosed" | null,
  });
  return {
    id: `dependents-${employeeId}`,
    title: "Dependents",
    singular: "dependent",
    schema: dependentSchema,
    defaults: { name: "", relationship: "spouse", date_of_birth: "", gender: "" },
    toForm: (row) => ({
      name: row.name,
      relationship: row.relationship,
      date_of_birth: row.date_of_birth ?? "",
      gender: row.gender ?? "",
    }),
    Fields: ({ form }) => (
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField form={form} name="name" label="Name" />
        <SelectField form={form} name="relationship" label="Relationship" options={RELATIONSHIPS} />
        <TextField form={form} name="date_of_birth" label="Date of birth" hint="YYYY-MM-DD" />
        <SelectField
          form={form}
          name="gender"
          label="Gender"
          placeholder="Not given"
          options={GENDERS}
        />
      </div>
    ),
    columns: [
      {
        id: "name",
        header: "Name",
        hideable: false,
        cell: (row) => <span className="font-medium">{row.name}</span>,
      },
      {
        id: "relationship",
        header: "Relationship",
        cell: (row) => RELATIONSHIPS.find((r) => r.value === row.relationship)?.label,
      },
      { id: "dob", header: "Born", cell: (row) => row.date_of_birth ?? "—" },
    ],
    list: () =>
      call<DependentRow[]>(
        api().GET("/v1/employees/{employee_id}/dependents", {
          params: { path: { employee_id: employeeId } },
        }),
      ),
    create: (v, key) =>
      call(
        api().POST("/v1/employees/{employee_id}/dependents", {
          params: { path: { employee_id: employeeId } },
          body: body(v),
          headers: { "Idempotency-Key": key },
        }),
      ),
    update: (row, v) =>
      call(
        api().PUT("/v1/employees/{employee_id}/dependents/{dependent_id}", {
          params: { path: { employee_id: employeeId, dependent_id: row.id } },
          body: { ...body(v), row_version: row.row_version },
        }),
      ),
    remove: (row) =>
      call(
        api().DELETE("/v1/employees/{employee_id}/dependents/{dependent_id}", {
          params: { path: { employee_id: employeeId, dependent_id: row.id } },
        }),
      ),
    rowLabel: (row) => row.name,
  };
}

// --- education and experience -------------------------------------------------------------------

const educationSchema = z.object({
  institution: required("the institution"),
  degree: required("the degree"),
  field_of_study: optionalText(200),
  start_year: year,
  end_year: year,
});
type EducationValues = z.infer<typeof educationSchema>;

interface EducationRow {
  id: string;
  institution: string;
  degree: string;
  field_of_study?: string | null;
  start_year?: number | null;
  end_year?: number | null;
  row_version: number;
}

export function education(employeeId: string): Resource<EducationRow, EducationValues> {
  const body = (v: EducationValues) => ({
    institution: v.institution,
    degree: v.degree,
    field_of_study: orNull(v.field_of_study),
    start_year: v.start_year === "" ? null : Number(v.start_year),
    end_year: v.end_year === "" ? null : Number(v.end_year),
  });
  return {
    id: `education-${employeeId}`,
    title: "Education",
    singular: "education entry",
    schema: educationSchema,
    defaults: { institution: "", degree: "", field_of_study: "", start_year: "", end_year: "" },
    toForm: (row) => ({
      institution: row.institution,
      degree: row.degree,
      field_of_study: row.field_of_study ?? "",
      start_year: row.start_year == null ? "" : String(row.start_year),
      end_year: row.end_year == null ? "" : String(row.end_year),
    }),
    Fields: ({ form }) => (
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField form={form} name="institution" label="Institution" />
        <TextField form={form} name="degree" label="Degree" />
        <TextField
          form={form}
          name="field_of_study"
          label="Field of study"
          className="sm:col-span-2"
        />
        <TextField form={form} name="start_year" label="From (year)" />
        <TextField form={form} name="end_year" label="To (year)" />
      </div>
    ),
    columns: [
      {
        id: "degree",
        header: "Degree",
        hideable: false,
        cell: (row) => <span className="font-medium">{row.degree}</span>,
      },
      { id: "institution", header: "Institution", cell: (row) => row.institution },
      {
        id: "years",
        header: "Years",
        cell: (row) =>
          row.start_year || row.end_year ? `${row.start_year ?? ""}–${row.end_year ?? ""}` : "—",
      },
    ],
    list: () =>
      call<EducationRow[]>(
        api().GET("/v1/employees/{employee_id}/education", {
          params: { path: { employee_id: employeeId } },
        }),
      ),
    create: (v, key) =>
      call(
        api().POST("/v1/employees/{employee_id}/education", {
          params: { path: { employee_id: employeeId } },
          body: body(v),
          headers: { "Idempotency-Key": key },
        }),
      ),
    update: (row, v) =>
      call(
        api().PUT("/v1/employees/{employee_id}/education/{entry_id}", {
          params: { path: { employee_id: employeeId, entry_id: row.id } },
          body: { ...body(v), row_version: row.row_version },
        }),
      ),
    remove: (row) =>
      call(
        api().DELETE("/v1/employees/{employee_id}/education/{entry_id}", {
          params: { path: { employee_id: employeeId, entry_id: row.id } },
        }),
      ),
    rowLabel: (row) => row.degree,
  };
}

const experienceSchema = z.object({
  employer: required("the employer"),
  title: required("the job title"),
  from_date: date,
  to_date: date,
});
type ExperienceValues = z.infer<typeof experienceSchema>;

interface ExperienceRow {
  id: string;
  employer: string;
  title: string;
  from_date?: string | null;
  to_date?: string | null;
  row_version: number;
}

export function experience(employeeId: string): Resource<ExperienceRow, ExperienceValues> {
  const body = (v: ExperienceValues) => ({
    employer: v.employer,
    title: v.title,
    from_date: orNull(v.from_date),
    to_date: orNull(v.to_date),
  });
  return {
    id: `experience-${employeeId}`,
    title: "Experience",
    singular: "experience entry",
    schema: experienceSchema,
    defaults: { employer: "", title: "", from_date: "", to_date: "" },
    toForm: (row) => ({
      employer: row.employer,
      title: row.title,
      from_date: row.from_date ?? "",
      to_date: row.to_date ?? "",
    }),
    Fields: ({ form }) => (
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField form={form} name="employer" label="Employer" />
        <TextField form={form} name="title" label="Job title" />
        <TextField form={form} name="from_date" label="From" hint="YYYY-MM-DD" />
        <TextField form={form} name="to_date" label="To" hint="YYYY-MM-DD" />
      </div>
    ),
    columns: [
      {
        id: "title",
        header: "Role",
        hideable: false,
        cell: (row) => <span className="font-medium">{row.title}</span>,
      },
      { id: "employer", header: "Employer", cell: (row) => row.employer },
      {
        id: "period",
        header: "Period",
        cell: (row) => `${row.from_date ?? "…"} – ${row.to_date ?? "…"}`,
      },
    ],
    list: () =>
      call<ExperienceRow[]>(
        api().GET("/v1/employees/{employee_id}/experience", {
          params: { path: { employee_id: employeeId } },
        }),
      ),
    create: (v, key) =>
      call(
        api().POST("/v1/employees/{employee_id}/experience", {
          params: { path: { employee_id: employeeId } },
          body: body(v),
          headers: { "Idempotency-Key": key },
        }),
      ),
    update: (row, v) =>
      call(
        api().PUT("/v1/employees/{employee_id}/experience/{entry_id}", {
          params: { path: { employee_id: employeeId, entry_id: row.id } },
          body: { ...body(v), row_version: row.row_version },
        }),
      ),
    remove: (row) =>
      call(
        api().DELETE("/v1/employees/{employee_id}/experience/{entry_id}", {
          params: { path: { employee_id: employeeId, entry_id: row.id } },
        }),
      ),
    rowLabel: (row) => row.title,
  };
}
