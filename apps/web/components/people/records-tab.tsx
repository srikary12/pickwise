// SPDX-License-Identifier: AGPL-3.0-only
"use client";

// Education, experience and the documents kept on the record (offer letter, ID proofs, …).
import type { DocumentOut } from "@pickwise/api-client";
import {
  Alert,
  Button,
  DataTable,
  type DataTableColumn,
  Dialog,
  FileUpload,
  Form,
  SelectField,
  TextField,
  useZodForm,
} from "@pickwise/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { z } from "zod";

import { ResourcePanel } from "@/components/org/resource-panel";
import { education, experience } from "@/components/people/resources";
import { api, call, errorMessage } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { uploadAndScan } from "@/lib/upload";

const CATEGORIES = [
  { value: "offer_letter", label: "Offer letter" },
  { value: "appointment_letter", label: "Appointment letter" },
  { value: "id_proof", label: "ID proof" },
  { value: "address_proof", label: "Address proof" },
  { value: "education", label: "Education" },
  { value: "experience", label: "Experience" },
  { value: "policy_ack", label: "Policy acknowledgement" },
  { value: "other", label: "Other" },
] as const;

const schema = z.object({
  category: z.string().min(1),
  title: z.string().trim().min(1, "Enter a title.").max(200),
  visible_to_employee: z.boolean(),
});
type Values = z.infer<typeof schema>;

function AddDocument({ employeeId, onClose }: { employeeId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [fileId, setFileId] = useState<string | null>(null);
  const [stage, setStage] = useState<string | null>(null);
  const form = useZodForm(schema, { category: "other", title: "", visible_to_employee: false });
  const add = useApiMutation({
    mutationFn: (v: Values, { idempotencyKey }) =>
      call(
        api().POST("/v1/employees/{employee_id}/documents", {
          params: { path: { employee_id: employeeId } },
          headers: { "Idempotency-Key": idempotencyKey },
          body: {
            category: v.category as DocumentOut["category"],
            title: v.title,
            visible_to_employee: v.visible_to_employee,
            file_id: fileId ?? "",
          },
        }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["employee", employeeId, "documents"] });
      onClose();
    },
  });
  return (
    <Form
      form={form}
      label="Add a document"
      onSubmit={async (values) => {
        if (!fileId) {
          setStage("Choose a file first.");
          return;
        }
        await add.mutateAsync(values).catch(() => undefined);
      }}
    >
      <FileUpload
        label="File"
        hint="PDF or an image. It is scanned for viruses before it can be used."
        upload={(file, report, signal) =>
          uploadAndScan(file, {
            classification: "confidential",
            owner: { type: "employee_document", id: employeeId },
            onStage: report,
            signal,
          })
        }
        onUploaded={(id) => {
          setFileId(id);
          setStage(null);
        }}
      />
      {stage ? (
        <p role="alert" className="text-sm text-destructive">
          {stage}
        </p>
      ) : null}
      <SelectField form={form} name="category" label="Category" options={CATEGORIES} />
      <TextField form={form} name="title" label="Title" />
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="size-4 accent-primary"
          {...form.register("visible_to_employee")}
        />
        Show this to the employee
      </label>
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

function Documents({ employeeId, canEdit }: { employeeId: string; canEdit: boolean }) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const docs = useQuery({
    queryKey: ["employee", employeeId, "documents"],
    queryFn: () =>
      call<DocumentOut[]>(
        api().GET("/v1/employees/{employee_id}/documents", {
          params: { path: { employee_id: employeeId } },
        }),
      ),
  });

  async function download(fileId: string) {
    try {
      const link = await call(
        api().GET("/v1/files/{file_id}/download", { params: { path: { file_id: fileId } } }),
      );
      window.open(link.url, "_blank", "noopener");
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }

  const columns: DataTableColumn<DocumentOut>[] = [
    {
      id: "title",
      header: "Document",
      hideable: false,
      cell: (r) => <span className="font-medium">{r.title}</span>,
    },
    {
      id: "category",
      header: "Category",
      cell: (r) => CATEGORIES.find((c) => c.value === r.category)?.label,
    },
    {
      id: "shared",
      header: "Employee can see",
      cell: (r) => (r.visible_to_employee ? "Yes" : "No"),
    },
    {
      id: "actions",
      header: "Actions",
      hideable: false,
      className: "text-right",
      cell: (r) => (
        <div className="flex justify-end gap-2">
          <Button
            size="sm"
            variant="outline"
            aria-label={`Download ${r.title}`}
            onClick={() => void download(r.file_id)}
          >
            Download
          </Button>
          {canEdit ? (
            <Button
              size="sm"
              variant="outline"
              aria-label={`Delete ${r.title}`}
              onClick={() =>
                void call(
                  api().DELETE("/v1/employees/{employee_id}/documents/{document_id}", {
                    params: { path: { employee_id: employeeId, document_id: r.id } },
                  }),
                )
                  .catch((error) => setNotice(errorMessage(error)))
                  .then(() =>
                    queryClient.invalidateQueries({
                      queryKey: ["employee", employeeId, "documents"],
                    }),
                  )
              }
            >
              Delete
            </Button>
          ) : null}
        </div>
      ),
    },
  ];
  return (
    <section aria-labelledby="documents-heading" className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 id="documents-heading" className="text-lg font-medium">
          Documents
        </h3>
        {canEdit ? <Button onClick={() => setAdding(true)}>Add document</Button> : null}
      </div>
      {notice ? <Alert variant="destructive">{notice}</Alert> : null}
      <DataTable
        caption="Documents"
        columns={columns}
        rows={docs.data ?? []}
        getRowId={(r) => r.id}
        rowTestId={(r) => `document-${r.title}`}
        loading={docs.isLoading}
        error={docs.isError ? errorMessage(docs.error) : null}
        emptyMessage="No documents yet."
      />
      <Dialog open={adding} onClose={() => setAdding(false)} title="Add a document">
        {adding ? <AddDocument employeeId={employeeId} onClose={() => setAdding(false)} /> : null}
      </Dialog>
    </section>
  );
}

export function RecordsTab({ employeeId, canEdit }: { employeeId: string; canEdit: boolean }) {
  return (
    <div className="flex flex-col gap-8" data-testid="records-tab">
      <section aria-labelledby="education-heading" className="flex flex-col gap-3">
        <h3 id="education-heading" className="text-lg font-medium">
          Education
        </h3>
        <ResourcePanel resource={education(employeeId)} canManage={canEdit} />
      </section>
      <section aria-labelledby="experience-heading" className="flex flex-col gap-3">
        <h3 id="experience-heading" className="text-lg font-medium">
          Experience
        </h3>
        <ResourcePanel resource={experience(employeeId)} canManage={canEdit} />
      </section>
      <Documents employeeId={employeeId} canEdit={canEdit} />
    </div>
  );
}
