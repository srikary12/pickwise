// SPDX-License-Identifier: AGPL-3.0-only
"use client";

// Identity documents and bank accounts. Numbers are always masked; "Reveal" (for people allowed
// to) asks the server, which audits it. New numbers go in once and never come back.
import type { BankOut, IdentityOut } from "@pickwise/api-client";
import {
  Alert,
  Badge,
  Button,
  DataTable,
  type DataTableColumn,
  Dialog,
  Form,
  formatDate,
  MaskedField,
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
import { isStaleVersion } from "@/lib/query";

const DOC_TYPES = [
  { value: "pan", label: "PAN" },
  { value: "aadhaar", label: "Aadhaar" },
  { value: "passport", label: "Passport" },
  { value: "uan", label: "UAN" },
  { value: "esic_ip", label: "ESIC IP number" },
  { value: "voter_id", label: "Voter ID" },
  { value: "driving_licence", label: "Driving licence" },
  { value: "visa", label: "Visa" },
] as const;
const typeLabel = (value: string) => DOC_TYPES.find((t) => t.value === value)?.label ?? value;
const orNull = (value: string): string | null => (value === "" ? null : value);

const identitySchema = z.object({
  doc_type: z.string().min(1),
  value: z.string().trim().min(4, "Enter the number.").max(40),
  name_as_per_doc: z.string().trim().max(200),
  expires_on: z
    .string()
    .refine((v) => v === "" || /^\d{4}-\d{2}-\d{2}$/.test(v), "Enter a valid date."),
});
type IdentityValues = z.infer<typeof identitySchema>;

function AddIdentity({ employeeId, onClose }: { employeeId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const form = useZodForm(identitySchema, {
    doc_type: "pan",
    value: "",
    name_as_per_doc: "",
    expires_on: "",
  });
  const add = useApiMutation({
    mutationFn: (v: IdentityValues, { idempotencyKey }) =>
      call(
        api().POST("/v1/employees/{employee_id}/identity", {
          params: { path: { employee_id: employeeId } },
          headers: { "Idempotency-Key": idempotencyKey },
          body: {
            doc_type: v.doc_type as IdentityOut["doc_type"],
            value: v.value,
            name_as_per_doc: orNull(v.name_as_per_doc),
            expires_on: orNull(v.expires_on),
          },
        }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["employee", employeeId, "identity"] });
      onClose();
    },
  });
  return (
    <Form
      form={form}
      label="Add an identity document"
      onSubmit={async (values) => {
        await add.mutateAsync(values).catch(() => undefined);
      }}
    >
      <SelectField form={form} name="doc_type" label="Document" options={DOC_TYPES} />
      <TextField
        form={form}
        name="value"
        label="Number"
        hint="Stored encrypted. It won't be shown again, only its last four characters."
      />
      <TextField form={form} name="name_as_per_doc" label="Name as on the document" />
      <TextField form={form} name="expires_on" label="Expires on" hint="YYYY-MM-DD" />
      {add.error ? <Alert variant="destructive">{errorMessage(add.error)}</Alert> : null}
      <div className="flex justify-end gap-3">
        <Button variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" disabled={add.isPending}>
          Save
        </Button>
      </div>
    </Form>
  );
}

const bankSchema = z.object({
  account_holder_name: z.string().trim().min(1, "Enter the account holder's name.").max(200),
  account_number: z
    .string()
    .trim()
    .regex(/^[0-9]{6,24}$/, "Enter digits only, 6 to 24."),
  ifsc: z
    .string()
    .trim()
    .toUpperCase()
    .regex(/^[A-Z]{4}0[A-Z0-9]{6}$/, "An IFSC looks like HDFC0001234."),
  bank_name: z.string().trim().min(1, "Enter the bank's name.").max(200),
  account_type: z.string().min(1),
  is_primary: z.boolean(),
  valid_from: z
    .string()
    .refine((v) => v === "" || /^\d{4}-\d{2}-\d{2}$/.test(v), "Enter a valid date."),
});
type BankValues = z.infer<typeof bankSchema>;

function AddBank({ employeeId, onClose }: { employeeId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const form = useZodForm(bankSchema, {
    account_holder_name: "",
    account_number: "",
    ifsc: "",
    bank_name: "",
    account_type: "savings",
    is_primary: true,
    valid_from: "",
  });
  const add = useApiMutation({
    mutationFn: (v: BankValues, { idempotencyKey }) =>
      call(
        api().POST("/v1/employees/{employee_id}/bank-accounts", {
          params: { path: { employee_id: employeeId } },
          headers: { "Idempotency-Key": idempotencyKey },
          body: {
            account_holder_name: v.account_holder_name,
            account_number: v.account_number,
            ifsc: v.ifsc,
            bank_name: v.bank_name,
            account_type: v.account_type as BankOut["account_type"],
            is_primary: v.is_primary,
            valid_from: orNull(v.valid_from),
          },
        }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["employee", employeeId, "bank"] });
      onClose();
    },
  });
  return (
    <Form
      form={form}
      label="Add a bank account"
      onSubmit={async (values) => {
        await add.mutateAsync(values).catch(() => undefined);
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField form={form} name="account_holder_name" label="Account holder" />
        <TextField form={form} name="bank_name" label="Bank" />
        <TextField
          form={form}
          name="account_number"
          label="Account number"
          hint="Stored encrypted."
        />
        <TextField form={form} name="ifsc" label="IFSC" />
        <SelectField
          form={form}
          name="account_type"
          label="Type"
          options={[
            { value: "savings", label: "Savings" },
            { value: "current", label: "Current" },
            { value: "salary", label: "Salary" },
          ]}
        />
        <TextField
          form={form}
          name="valid_from"
          label="Use from"
          hint="YYYY-MM-DD; today if empty."
        />
        <label className="flex items-center gap-2 text-sm sm:col-span-2">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            {...form.register("is_primary")}
          />
          Salary goes to this account from that date
        </label>
      </div>
      {add.error ? <Alert variant="destructive">{errorMessage(add.error)}</Alert> : null}
      <div className="flex justify-end gap-3">
        <Button variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" disabled={add.isPending}>
          Save
        </Button>
      </div>
    </Form>
  );
}

async function reveal(entityType: string, id: string, field: string): Promise<string> {
  const result = await call(
    api().POST("/v1/pii/reveal", { body: { entity_type: entityType, entity_id: id, field } }),
  );
  if (result.value == null) throw new Error("There is nothing stored to show.");
  return result.value;
}

export function IdentityTab({
  employeeId,
  canEdit,
  canReveal,
  canBank,
  canEditBank,
  canRevealBank,
  canIdentity,
  self = false,
}: {
  employeeId: string;
  canEdit: boolean;
  canReveal: boolean;
  canBank: boolean;
  canEditBank: boolean;
  canRevealBank: boolean;
  canIdentity: boolean;
  self?: boolean;
}) {
  const queryClient = useQueryClient();
  const [addingId, setAddingId] = useState(false);
  const [addingBank, setAddingBank] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const identity = useQuery({
    queryKey: ["employee", employeeId, "identity"],
    enabled: canIdentity,
    queryFn: () =>
      call<IdentityOut[]>(
        self
          ? api().GET("/v1/me/employee/identity")
          : api().GET("/v1/employees/{employee_id}/identity", {
              params: { path: { employee_id: employeeId }, query: {} },
            }),
      ),
  });
  const bank = useQuery({
    queryKey: ["employee", employeeId, "bank"],
    enabled: canBank,
    queryFn: () =>
      call<BankOut[]>(
        self
          ? api().GET("/v1/me/employee/bank-accounts")
          : api().GET("/v1/employees/{employee_id}/bank-accounts", {
              params: { path: { employee_id: employeeId } },
            }),
      ),
  });

  async function run(action: () => Promise<unknown>, key: string) {
    setNotice(null);
    try {
      await action();
    } catch (error) {
      if (isStaleVersion(error)) showConflict();
      else setNotice(errorMessage(error));
    }
    await queryClient.invalidateQueries({ queryKey: ["employee", employeeId, key] });
  }

  const identityColumns: DataTableColumn<IdentityOut>[] = [
    {
      id: "doc",
      header: "Document",
      hideable: false,
      cell: (row) =>
        row.has_value && canReveal && !self ? (
          <MaskedField
            label={typeLabel(row.doc_type)}
            masked={`••••${row.last4 ?? ""}`}
            reveal={() => reveal("identity_document", row.id, "value")}
          />
        ) : (
          <>
            <span className="font-medium">{typeLabel(row.doc_type)}</span>
            <div className="font-mono text-sm tabular-nums" data-testid="masked-value">
              ••••{row.last4 ?? ""}
            </div>
            {!row.has_value ? (
              <div className="text-xs text-muted-foreground">Last four digits only</div>
            ) : null}
          </>
        ),
    },
    {
      id: "status",
      header: "Status",
      cell: (row) => (
        <Badge
          variant={
            row.verification_status === "verified"
              ? "success"
              : row.verification_status === "rejected"
                ? "destructive"
                : "outline"
          }
        >
          {row.verification_status}
        </Badge>
      ),
    },
    {
      id: "expires",
      header: "Expires",
      cell: (row) => (row.expires_on ? formatDate(row.expires_on) : "—"),
    },
    ...(canEdit && !self
      ? [
          {
            id: "actions",
            header: "Actions",
            hideable: false,
            className: "text-right",
            cell: (row: IdentityOut) =>
              row.verification_status === "unverified" ? (
                <Button
                  size="sm"
                  variant="outline"
                  aria-label={`Mark ${typeLabel(row.doc_type)} verified`}
                  onClick={() =>
                    void run(
                      () =>
                        call(
                          api().POST("/v1/employees/{employee_id}/identity/{document_id}/verify", {
                            params: { path: { employee_id: employeeId, document_id: row.id } },
                            body: { status: "verified", row_version: row.row_version },
                          }),
                        ),
                      "identity",
                    )
                  }
                >
                  Verify
                </Button>
              ) : null,
          } satisfies DataTableColumn<IdentityOut>,
        ]
      : []),
  ];

  const bankColumns: DataTableColumn<BankOut>[] = [
    {
      id: "account",
      header: "Account",
      hideable: false,
      cell: (row) =>
        canRevealBank && !self ? (
          <MaskedField
            label={`${row.bank_name} · ${row.account_holder_name}`}
            masked={`••••${row.last4}`}
            reveal={() => reveal("bank_account", row.id, "account_number")}
          />
        ) : (
          <>
            <span className="font-medium">
              {row.bank_name} · {row.account_holder_name}
            </span>
            <div className="font-mono text-sm tabular-nums" data-testid="masked-value">
              ••••{row.last4}
            </div>
          </>
        ),
    },
    { id: "ifsc", header: "IFSC", cell: (row) => row.ifsc },
    {
      id: "use",
      header: "In use",
      cell: (row) => (
        <>
          {row.is_primary ? <Badge className="mr-1">Primary</Badge> : null}
          {formatDate(row.valid_from)} – {row.valid_to ? formatDate(row.valid_to) : "now"}
        </>
      ),
    },
    ...(canEditBank && !self
      ? [
          {
            id: "actions",
            header: "Actions",
            hideable: false,
            className: "text-right",
            cell: (row: BankOut) =>
              row.valid_to == null ? (
                <Button
                  size="sm"
                  variant="outline"
                  aria-label={`Stop using the account ending ${row.last4}`}
                  onClick={() =>
                    void run(
                      () =>
                        call(
                          api().POST("/v1/employees/{employee_id}/bank-accounts/{account_id}/end", {
                            params: {
                              path: { employee_id: employeeId, account_id: row.id },
                              query: { row_version: row.row_version },
                            },
                          }),
                        ),
                      "bank",
                    )
                  }
                >
                  Stop using
                </Button>
              ) : null,
          } satisfies DataTableColumn<BankOut>,
        ]
      : []),
  ];

  return (
    <div className="flex flex-col gap-8" data-testid="identity-tab">
      {notice ? <Alert variant="destructive">{notice}</Alert> : null}
      {canIdentity ? (
        <section aria-labelledby="identity-heading" className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h3 id="identity-heading" className="text-lg font-medium">
              Identity documents
            </h3>
            {canEdit && !self ? (
              <Button onClick={() => setAddingId(true)}>Add document</Button>
            ) : null}
          </div>
          <DataTable
            caption="Identity documents"
            columns={identityColumns}
            rows={identity.data ?? []}
            getRowId={(r) => r.id}
            rowTestId={(r) => `identity-${r.doc_type}`}
            loading={identity.isLoading}
            error={identity.isError ? errorMessage(identity.error) : null}
            emptyMessage="No identity documents yet."
          />
          <Dialog
            open={addingId}
            onClose={() => setAddingId(false)}
            title="Add an identity document"
          >
            {addingId ? (
              <AddIdentity employeeId={employeeId} onClose={() => setAddingId(false)} />
            ) : null}
          </Dialog>
        </section>
      ) : null}
      {canBank ? (
        <section aria-labelledby="bank-heading" className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h3 id="bank-heading" className="text-lg font-medium">
              Bank accounts
            </h3>
            {canEditBank && !self ? (
              <Button onClick={() => setAddingBank(true)}>Add account</Button>
            ) : null}
          </div>
          <DataTable
            caption="Bank accounts"
            columns={bankColumns}
            rows={bank.data ?? []}
            getRowId={(r) => r.id}
            rowTestId={(r) => `bank-${r.last4}`}
            loading={bank.isLoading}
            error={bank.isError ? errorMessage(bank.error) : null}
            emptyMessage="No bank accounts yet."
          />
          <Dialog
            open={addingBank}
            onClose={() => setAddingBank(false)}
            title="Add a bank account"
            className="max-w-2xl"
          >
            {addingBank ? (
              <AddBank employeeId={employeeId} onClose={() => setAddingBank(false)} />
            ) : null}
          </Dialog>
        </section>
      ) : null}
    </div>
  );
}
