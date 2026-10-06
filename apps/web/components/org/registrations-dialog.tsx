// SPDX-License-Identifier: AGPL-3.0-only
"use client";

// A legal entity's statutory registrations (professional tax, labour welfare fund, shops and
// establishment, factory), each valid for a period. Periods for one state and type can't overlap;
// the server says so, and the message is shown here.
import type { LegalEntityOut, RegistrationOut } from "@pickwise/api-client";
import {
  Alert,
  Button,
  DataTable,
  type DataTableColumn,
  DateField,
  Dialog,
  Form,
  formatDate,
  SelectField,
  TextField,
  dateSchema,
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

const TYPES = [
  { value: "PT", label: "Professional tax" },
  { value: "LWF", label: "Labour welfare fund" },
  { value: "SHOPS_ESTABLISHMENT", label: "Shops and establishment" },
  { value: "FACTORY", label: "Factory" },
] as const;

const schema = z
  .object({
    registration_type: z.enum(["PT", "LWF", "SHOPS_ESTABLISHMENT", "FACTORY"]),
    state_code: z.string().min(1, "Choose a state."),
    registration_no: z.string().trim().min(1, "Enter the registration number.").max(60),
    valid_from: dateSchema("Enter the start date."),
    valid_to: z
      .string()
      .refine(
        (v) => v === "" || !Number.isNaN(Date.parse(`${v}T00:00:00Z`)),
        "Enter a valid date.",
      ),
  })
  .refine((v) => v.valid_to === "" || v.valid_to >= v.valid_from, {
    message: "The end can't be before the start.",
    path: ["valid_to"],
  });

function AddForm({ entityId }: { entityId: string }) {
  const queryClient = useQueryClient();
  const form = useZodForm(schema, {
    registration_type: "PT",
    state_code: "",
    registration_no: "",
    valid_from: "",
    valid_to: "",
  });
  const add = useApiMutation({
    mutationFn: (values: z.infer<typeof schema>, { idempotencyKey }) =>
      call(
        api().POST("/v1/org/legal-entities/{entity_id}/registrations", {
          params: { path: { entity_id: entityId } },
          body: { ...values, valid_to: values.valid_to === "" ? null : values.valid_to },
          headers: { "Idempotency-Key": idempotencyKey },
        }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["org", "registrations", entityId] });
      form.reset();
    },
  });
  return (
    <Form
      form={form}
      label="Add a registration"
      onSubmit={async (values) => {
        await add.mutateAsync(values).catch(() => undefined);
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectField form={form} name="registration_type" label="Type" options={TYPES} />
        <SelectField
          form={form}
          name="state_code"
          label="State"
          placeholder="Choose…"
          options={INDIAN_STATES}
        />
        <TextField form={form} name="registration_no" label="Registration number" />
        <div />
        <DateField form={form} name="valid_from" label="Valid from" />
        <DateField
          form={form}
          name="valid_to"
          label="Valid until"
          hint="Leave empty if it has no end."
        />
      </div>
      {add.error ? (
        <Alert variant="destructive" data-testid="registration-error">
          {errorMessage(add.error)}
        </Alert>
      ) : null}
      <div className="flex justify-end">
        <Button type="submit" disabled={add.isPending}>
          Add registration
        </Button>
      </div>
    </Form>
  );
}

function Registrations({ entity, canManage }: { entity: LegalEntityOut; canManage: boolean }) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const rows = useQuery({
    queryKey: ["org", "registrations", entity.id],
    queryFn: () =>
      call<RegistrationOut[]>(
        api().GET("/v1/org/legal-entities/{entity_id}/registrations", {
          params: { path: { entity_id: entity.id } },
        }),
      ),
  });

  async function remove(row: RegistrationOut) {
    setNotice(null);
    try {
      await call(
        api().DELETE("/v1/org/legal-entities/{entity_id}/registrations/{registration_id}", {
          params: { path: { entity_id: entity.id, registration_id: row.id } },
        }),
      );
    } catch (error) {
      if (isStaleVersion(error)) showConflict();
      else setNotice(errorMessage(error));
    }
    await queryClient.invalidateQueries({ queryKey: ["org", "registrations", entity.id] });
  }

  const columns: DataTableColumn<RegistrationOut>[] = [
    {
      id: "type",
      header: "Type",
      cell: (row) => TYPES.find((t) => t.value === row.registration_type)?.label,
    },
    { id: "state", header: "State", cell: (row) => stateName(row.state_code) },
    { id: "no", header: "Number", cell: (row) => row.registration_no },
    {
      id: "period",
      header: "Valid",
      cell: (row) =>
        `${formatDate(row.valid_from)} – ${row.valid_to ? formatDate(row.valid_to) : "no end"}`,
    },
    ...(canManage
      ? [
          {
            id: "actions",
            header: "Actions",
            hideable: false,
            className: "text-right",
            cell: (row: RegistrationOut) => (
              <Button
                size="sm"
                variant="outline"
                aria-label={`Delete registration ${row.registration_no}`}
                onClick={() => void remove(row)}
              >
                Delete
              </Button>
            ),
          } satisfies DataTableColumn<RegistrationOut>,
        ]
      : []),
  ];

  return (
    <div className="flex flex-col gap-4">
      {notice ? <Alert variant="destructive">{notice}</Alert> : null}
      <DataTable
        caption={`Registrations of ${entity.name}`}
        columns={columns}
        rows={rows.data ?? []}
        getRowId={(row) => row.id}
        loading={rows.isLoading}
        error={rows.isError ? errorMessage(rows.error) : null}
        emptyMessage="No registrations yet."
      />
      {canManage ? <AddForm entityId={entity.id} /> : null}
    </div>
  );
}

export function RegistrationsButton({
  entity,
  canManage,
}: {
  entity: LegalEntityOut;
  canManage: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        size="sm"
        variant="outline"
        aria-label={`Registrations of ${entity.name}`}
        onClick={() => setOpen(true)}
      >
        Registrations
      </Button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title={`Registrations of ${entity.name}`}
        className="max-w-3xl"
      >
        <Registrations entity={entity} canManage={canManage} />
        <div className="mt-4 flex justify-end">
          <Button variant="outline" onClick={() => setOpen(false)}>
            Close
          </Button>
        </div>
      </Dialog>
    </>
  );
}
