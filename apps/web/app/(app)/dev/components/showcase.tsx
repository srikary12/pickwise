// SPDX-License-Identifier: AGPL-3.0-only
"use client";

// A showcase of the shared components with fixed sample data, for the accessibility checks and
// for people building screens. page.tsx hides it in production.
import {
  ApprovalPanel,
  type ApprovalRequestView,
  AuditTrail,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  DataTable,
  type DataTableColumn,
  dateSchema,
  DateField,
  EffectiveDatedTimeline,
  FileUpload,
  Form,
  formatCompactCurrency,
  formatCurrency,
  formatDate,
  formatNumber,
  MaskedField,
  MoneyField,
  moneySchema,
  type Sort,
  TextField,
  useZodForm,
} from "@pickwise/ui";
import { useMemo, useState } from "react";
import { z } from "zod";

interface Person {
  id: string;
  name: string;
  department: string;
  salary: string;
}

const PEOPLE: Person[] = [
  { id: "1", name: "Asha Rao", department: "Engineering", salary: "1850000" },
  { id: "2", name: "Bala Iyer", department: "Finance", salary: "1200000.50" },
  { id: "3", name: "Chitra Nair", department: "People", salary: "950000" },
];

const COLUMNS: DataTableColumn<Person>[] = [
  { id: "name", header: "Name", sortable: true, hideable: false, cell: (p) => p.name },
  { id: "department", header: "Department", sortable: true, cell: (p) => p.department },
  {
    id: "salary",
    header: "Annual pay",
    className: "text-right tabular-nums",
    cell: (p) => formatCurrency(p.salary),
  },
];

const REQUEST: ApprovalRequestView = {
  policy_name: "Leave approval",
  entity_type: "leave_request",
  status: "pending",
  requested_by: { name: "Asha Rao" },
  created_at: "2026-10-01T05:30:00Z",
  my_task_id: "task-1",
  can_cancel: false,
  steps: [
    {
      step_no: 1,
      name: "Manager",
      mode: "any",
      state: "done",
      tasks: [
        {
          id: "t0",
          assignee: { name: "Bala Iyer" },
          status: "approved",
          acted_at: "2026-10-02T04:00:00Z",
          comment: "Coverage is arranged.",
          delegated_from: null,
        },
      ],
    },
    {
      step_no: 2,
      name: "HR",
      mode: "any",
      state: "current",
      tasks: [
        {
          id: "task-1",
          assignee: { name: "Chitra Nair" },
          status: "pending",
          acted_at: null,
          comment: null,
          delegated_from: null,
        },
      ],
    },
  ],
};

const schema = z.object({
  name: z.string().trim().min(1, "Enter a name."),
  amount: moneySchema(),
  start: dateSchema(),
});

export function Showcase() {
  const [sort, setSort] = useState<Sort | null>(null);
  const [submitted, setSubmitted] = useState<string | null>(null);
  const rows = useMemo(() => {
    if (!sort) return PEOPLE;
    const key = sort.id as "name" | "department";
    return [...PEOPLE].sort((a, b) => a[key].localeCompare(b[key]) * (sort.desc ? -1 : 1));
  }, [sort]);
  const form = useZodForm(schema, { name: "", amount: "", start: "" });

  return (
    <div className="flex flex-col gap-8">
      <h1 className="text-2xl font-semibold">Components</h1>

      <section aria-labelledby="c-table" className="flex flex-col gap-3">
        <h2 id="c-table" className="text-lg font-semibold">
          DataTable
        </h2>
        <DataTable
          caption="Sample people"
          columns={COLUMNS}
          rows={rows}
          getRowId={(p) => p.id}
          rowTestId={(p) => `person-${p.id}`}
          sort={sort}
          onSortChange={setSort}
        />
      </section>

      <section aria-labelledby="c-form" className="flex flex-col gap-3">
        <h2 id="c-form" className="text-lg font-semibold">
          Form
        </h2>
        <Card>
          <CardContent className="pt-6">
            <Form
              form={form}
              label="Sample form"
              onSubmit={(values) => setSubmitted(JSON.stringify(values))}
            >
              <TextField form={form} name="name" label="Name" />
              <MoneyField form={form} name="amount" label="Amount" hint="In rupees." />
              <DateField form={form} name="start" label="Start date" />
              <Button type="submit" className="self-start">
                Save
              </Button>
            </Form>
            {submitted ? (
              <p data-testid="form-result" className="mt-3 font-mono text-xs">
                {submitted}
              </p>
            ) : null}
          </CardContent>
        </Card>
      </section>

      <section aria-labelledby="c-timeline" className="flex flex-col gap-3">
        <h2 id="c-timeline" className="text-lg font-semibold">
          EffectiveDatedTimeline
        </h2>
        <EffectiveDatedTimeline
          label="Job history"
          today="2026-10-06"
          items={[
            { id: "a", validFrom: "2023-04-01", validTo: "2024-03-31", title: "Engineer" },
            { id: "b", validFrom: "2024-06-01", validTo: "2026-03-31", title: "Senior Engineer" },
            { id: "c", validFrom: "2026-04-01", validTo: null, title: "Staff Engineer" },
          ]}
        />
      </section>

      <section aria-labelledby="c-upload" className="flex flex-col gap-3">
        <h2 id="c-upload" className="text-lg font-semibold">
          FileUpload
        </h2>
        <FileUpload
          label="Sample document"
          hint="Nothing is sent anywhere."
          upload={async (file, report) => {
            report("uploading");
            await new Promise((resolve) => setTimeout(resolve, 150));
            report("scanning");
            await new Promise((resolve) => setTimeout(resolve, 150));
            if (file.name.includes("eicar")) throw new Error("The file was rejected: virus found.");
            return "sample-file-id";
          }}
          onUploaded={() => undefined}
        />
      </section>

      <section aria-labelledby="c-approval" className="flex flex-col gap-3">
        <h2 id="c-approval" className="text-lg font-semibold">
          ApprovalPanel
        </h2>
        <ApprovalPanel request={REQUEST} onDecide={() => undefined} />
      </section>

      <section aria-labelledby="c-audit" className="flex flex-col gap-3">
        <h2 id="c-audit" className="text-lg font-semibold">
          AuditTrail
        </h2>
        <AuditTrail
          events={[
            {
              id: "e1",
              occurred_at: "2026-10-05T10:00:00Z",
              actor_name: "Asha Rao",
              actor_type: "user",
              action: "update",
              changes: { department: ["Finance", "People"], pan_enc: "[changed]" },
            },
          ]}
        />
      </section>

      <section aria-labelledby="c-masked" className="flex flex-col gap-3">
        <h2 id="c-masked" className="text-lg font-semibold">
          MaskedField
        </h2>
        <MaskedField
          label="PAN"
          masked="XXXXX1234X"
          reveal={async () => "ABCDE1234F"}
          showForMs={5000}
        />
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Formatting</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-1 text-sm">
          <p>{formatNumber(1234567.5)}</p>
          <p>{formatCurrency("150000")}</p>
          <p>{formatCompactCurrency(1250000)}</p>
          <p>{formatDate("2026-04-01")}</p>
        </CardContent>
      </Card>
    </div>
  );
}
