// SPDX-License-Identifier: AGPL-3.0-only
"use client";

// Personal details and addresses. HR edits all of it; a person editing their own record may change
// only their personal email and phone (and addresses), through /v1/me/employee.
import type { AddressOut, PersonalOut } from "@pickwise/api-client";
import {
  Alert,
  Button,
  Dialog,
  Form,
  formatDate,
  SelectField,
  TextField,
  useZodForm,
} from "@pickwise/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { z } from "zod";

import { api, call, errorMessage } from "@/lib/api";
import { showConflict } from "@/lib/conflict";
import { INDIAN_STATES, stateName } from "@/lib/india";
import { useApiMutation } from "@/lib/mutations";
import { isStaleVersion } from "@/lib/query";

const optional = (pattern: RegExp, message: string) =>
  z
    .string()
    .trim()
    .refine((value) => value === "" || pattern.test(value), message);
const orNull = (value: string): string | null => (value === "" ? null : value);

const GENDERS = [
  { value: "female", label: "Female" },
  { value: "male", label: "Male" },
  { value: "non_binary", label: "Non-binary" },
  { value: "undisclosed", label: "Prefer not to say" },
] as const;
const MARITAL = [
  { value: "single", label: "Single" },
  { value: "married", label: "Married" },
  { value: "divorced", label: "Divorced" },
  { value: "widowed", label: "Widowed" },
  { value: "undisclosed", label: "Prefer not to say" },
] as const;
const BLOOD = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"].map((v) => ({
  value: v,
  label: v,
}));

const schema = z.object({
  date_of_birth: optional(/^\d{4}-\d{2}-\d{2}$/, "Enter a valid date."),
  gender: z.string(),
  marital_status: z.string(),
  blood_group: z.string(),
  nationality: z.string().trim().max(60),
  father_or_spouse_name: z.string().trim().max(200),
  is_person_with_disability: z.boolean(),
  personal_email: optional(/^[^@\s]+@[^@\s]+\.[^@\s]+$/, "Enter a valid email address."),
  personal_phone: optional(/^[0-9+()\-\s]{5,30}$/, "Enter a phone number."),
});
type Values = z.infer<typeof schema>;

function PersonalForm({
  employeeId,
  personal,
  self,
  canEdit,
}: {
  employeeId: string;
  personal: PersonalOut;
  self: boolean;
  canEdit: boolean;
}) {
  const queryClient = useQueryClient();
  const [saved, setSaved] = useState(false);
  const form = useZodForm(schema, {
    date_of_birth: personal.date_of_birth ?? "",
    gender: personal.gender ?? "",
    marital_status: personal.marital_status ?? "",
    blood_group: personal.blood_group ?? "",
    nationality: personal.nationality ?? "",
    father_or_spouse_name: personal.father_or_spouse_name ?? "",
    is_person_with_disability: personal.is_person_with_disability,
    personal_email: personal.personal_email ?? "",
    personal_phone: personal.personal_phone ?? "",
  });
  const save = useApiMutation({
    mutationFn: (v: Values) =>
      self
        ? call(
            api().PUT("/v1/me/employee/contact-details", {
              body: {
                personal_email: orNull(v.personal_email),
                personal_phone: orNull(v.personal_phone),
              },
            }),
          )
        : call(
            api().PUT("/v1/employees/{employee_id}/personal", {
              params: { path: { employee_id: employeeId } },
              body: {
                date_of_birth: orNull(v.date_of_birth),
                gender: (v.gender || null) as NonNullable<PersonalOut["gender"]> | null,
                marital_status: (v.marital_status || null) as NonNullable<
                  PersonalOut["marital_status"]
                > | null,
                blood_group: (v.blood_group || null) as NonNullable<
                  PersonalOut["blood_group"]
                > | null,
                nationality: orNull(v.nationality),
                father_or_spouse_name: orNull(v.father_or_spouse_name),
                is_person_with_disability: v.is_person_with_disability,
                personal_email: orNull(v.personal_email),
                personal_phone: orNull(v.personal_phone),
                row_version: personal.row_version ?? null,
              },
            }),
          ),
    onSuccess: async () => {
      setSaved(true);
      await queryClient.invalidateQueries({ queryKey: ["employee", employeeId, "personal"] });
    },
    onError: (error) => {
      setSaved(false);
      if (isStaleVersion(error)) showConflict();
    },
  });
  // A person can edit only their contact details; HR edits everything.
  const hrOnly = self;
  return (
    <Form
      form={form}
      label="Personal details"
      onSubmit={async (values) => {
        setSaved(false);
        await save.mutateAsync(values).catch(() => undefined);
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField form={form} name="date_of_birth" label="Date of birth" hint="YYYY-MM-DD" />
        <SelectField
          form={form}
          name="gender"
          label="Gender"
          placeholder="Not given"
          options={GENDERS}
        />
        <SelectField
          form={form}
          name="marital_status"
          label="Marital status"
          placeholder="Not given"
          options={MARITAL}
        />
        <SelectField
          form={form}
          name="blood_group"
          label="Blood group"
          placeholder="Not given"
          options={BLOOD}
        />
        <TextField form={form} name="nationality" label="Nationality" />
        <TextField form={form} name="father_or_spouse_name" label="Father's or spouse's name" />
        <TextField form={form} name="personal_email" label="Personal email" type="email" />
        <TextField form={form} name="personal_phone" label="Personal phone" type="tel" />
        <label className="flex items-center gap-2 text-sm sm:col-span-2">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            {...form.register("is_person_with_disability")}
          />
          Person with disability
        </label>
      </div>
      {hrOnly ? (
        <p className="text-sm text-muted-foreground">
          Ask HR to change anything other than your email and phone.
        </p>
      ) : null}
      {save.error ? <Alert variant="destructive">{errorMessage(save.error)}</Alert> : null}
      {saved ? <Alert variant="success">Saved.</Alert> : null}
      {canEdit ? (
        <div className="flex justify-end">
          <Button type="submit" disabled={save.isPending}>
            Save
          </Button>
        </div>
      ) : null}
    </Form>
  );
}

const addressSchema = z.object({
  line1: z.string().trim().min(1, "Enter the address.").max(200),
  line2: z.string().trim().max(200),
  city: z.string().trim().min(1, "Enter the city.").max(100),
  state_code: z.string(),
  pincode: optional(/^[1-9][0-9]{5}$/, "A PIN code has 6 digits."),
});
type AddressValues = z.infer<typeof addressSchema>;

function AddressDialog({
  employeeId,
  type,
  current,
  self,
  open,
  onClose,
}: {
  employeeId: string;
  type: "current" | "permanent";
  current: AddressOut | undefined;
  self: boolean;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const form = useZodForm(addressSchema, {
    line1: current?.line1 ?? "",
    line2: current?.line2 ?? "",
    city: current?.city ?? "",
    state_code: current?.state_code ?? "",
    pincode: current?.pincode ?? "",
  });
  const save = useApiMutation({
    mutationFn: (v: AddressValues) => {
      const body = {
        line1: v.line1,
        line2: orNull(v.line2),
        city: v.city,
        state_code: orNull(v.state_code),
        pincode: orNull(v.pincode),
        country_code: "IN",
      };
      return call(
        self
          ? api().PUT("/v1/me/employee/addresses/{address_type}", {
              params: { path: { address_type: type } },
              body,
            })
          : api().PUT("/v1/employees/{employee_id}/addresses/{address_type}", {
              params: { path: { employee_id: employeeId, address_type: type } },
              body,
            }),
      );
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["employee", employeeId, "addresses"] });
      onClose();
    },
  });
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`${type === "current" ? "Current" : "Permanent"} address`}
    >
      <Form
        form={form}
        label="Address"
        onSubmit={async (values) => {
          await save.mutateAsync(values).catch(() => undefined);
        }}
      >
        <TextField form={form} name="line1" label="Address line 1" />
        <TextField form={form} name="line2" label="Address line 2" />
        <TextField form={form} name="city" label="City" />
        <SelectField
          form={form}
          name="state_code"
          label="State"
          placeholder="Choose…"
          options={INDIAN_STATES}
        />
        <TextField form={form} name="pincode" label="PIN code" />
        <p className="text-xs text-muted-foreground">
          This starts today; the previous address stays in the history.
        </p>
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
    </Dialog>
  );
}

function Addresses({
  employeeId,
  self,
  canEdit,
}: {
  employeeId: string;
  self: boolean;
  canEdit: boolean;
}) {
  const [editing, setEditing] = useState<"current" | "permanent" | null>(null);
  const addresses = useQuery({
    queryKey: ["employee", employeeId, "addresses"],
    queryFn: () =>
      call<AddressOut[]>(
        self
          ? api().GET("/v1/me/employee/addresses")
          : api().GET("/v1/employees/{employee_id}/addresses", {
              params: { path: { employee_id: employeeId } },
            }),
      ),
  });
  const rows = addresses.data ?? [];
  return (
    <section aria-labelledby="addresses-heading" className="flex flex-col gap-3">
      <h3 id="addresses-heading" className="text-lg font-medium">
        Addresses
      </h3>
      {addresses.isError ? (
        <Alert variant="destructive">{errorMessage(addresses.error)}</Alert>
      ) : null}
      <div className="grid gap-4 sm:grid-cols-2">
        {(["current", "permanent"] as const).map((type) => {
          const inForce = rows.find((r) => r.address_type === type && r.valid_to == null);
          const past = rows.filter((r) => r.address_type === type && r.valid_to != null);
          return (
            <div
              key={type}
              className="rounded-lg border border-border bg-card p-4"
              data-testid={`address-${type}`}
            >
              <div className="flex items-center justify-between">
                <h4 className="font-medium">{type === "current" ? "Current" : "Permanent"}</h4>
                {canEdit ? (
                  <Button size="sm" variant="outline" onClick={() => setEditing(type)}>
                    {inForce ? "Change" : "Add"}
                  </Button>
                ) : null}
              </div>
              {inForce ? (
                <p className="mt-2 text-sm">
                  {inForce.line1}
                  {inForce.line2 ? `, ${inForce.line2}` : ""}
                  <br />
                  {inForce.city}
                  {inForce.state_code ? `, ${stateName(inForce.state_code)}` : ""}{" "}
                  {inForce.pincode ?? ""}
                </p>
              ) : (
                <p className="mt-2 text-sm text-muted-foreground">Not recorded.</p>
              )}
              {past.length > 0 ? (
                <ul
                  className="mt-2 text-xs text-muted-foreground"
                  aria-label={`Earlier ${type} addresses`}
                >
                  {past.map((p) => (
                    <li key={p.id}>
                      {p.line1}, {p.city} (until {formatDate(p.valid_to ?? "")})
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          );
        })}
      </div>
      {editing ? (
        <AddressDialog
          key={editing}
          employeeId={employeeId}
          type={editing}
          current={rows.find((r) => r.address_type === editing && r.valid_to == null)}
          self={self}
          open
          onClose={() => setEditing(null)}
        />
      ) : null}
    </section>
  );
}

export function PersonalTab({
  employeeId,
  self = false,
  canEdit,
}: {
  employeeId: string;
  self?: boolean;
  canEdit: boolean;
}) {
  const personal = useQuery({
    queryKey: ["employee", employeeId, "personal"],
    queryFn: () =>
      call<PersonalOut>(
        self
          ? api().GET("/v1/me/employee/personal")
          : api().GET("/v1/employees/{employee_id}/personal", {
              params: { path: { employee_id: employeeId } },
            }),
      ),
  });
  if (personal.isError) return <Alert variant="destructive">{errorMessage(personal.error)}</Alert>;
  if (!personal.data)
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading…
      </p>
    );
  return (
    <div className="flex flex-col gap-8" data-testid="personal-tab">
      <PersonalForm
        employeeId={employeeId}
        personal={personal.data}
        self={self}
        canEdit={canEdit}
      />
      <Addresses employeeId={employeeId} self={self} canEdit={canEdit} />
    </div>
  );
}
