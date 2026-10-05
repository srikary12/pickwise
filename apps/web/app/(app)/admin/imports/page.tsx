// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { ImportOut, ImportTypeOut } from "@pickwise/api-client";
import { Alert, Badge, Button, Card, CardContent, Field, Select } from "@pickwise/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { NoAccess } from "@/components/no-access";
import { api, call, errorMessage } from "@/lib/api";
import { can, useSession } from "@/lib/session";
import { uploadAndScan } from "@/lib/upload";

const ACTIVE = new Set(["pending", "validating", "committing"]);
const STATUS_VARIANT = {
  validated: "success",
  committed: "success",
  failed: "destructive",
  cancelled: "outline",
} as const;

function stat(importOut: ImportOut, key: string): number {
  const value = importOut.stats[key];
  return typeof value === "number" ? value : 0;
}

function ImportCard({ item }: { item: ImportOut }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const errors = stat(item, "error_rows");
  const message = item.stats.error_message;

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      await queryClient.invalidateQueries({ queryKey: ["admin", "imports"] });
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <Card data-testid={`import-${item.id}`}>
      <CardContent className="flex flex-col gap-3 pt-6">
        <div className="flex items-center gap-3">
          <span className="font-medium">{item.import_type}</span>
          <Badge
            variant={STATUS_VARIANT[item.status as keyof typeof STATUS_VARIANT] ?? "warning"}
            data-testid="import-status"
          >
            {item.status}
          </Badge>
          <span className="ml-auto text-xs text-muted-foreground">
            {new Date(item.created_at).toLocaleString()}
          </span>
        </div>
        {item.status === "validated" ||
        item.status === "committed" ||
        item.status === "committing" ? (
          <p className="text-sm" data-testid="import-summary">
            {stat(item, "rows")} rows: {stat(item, "valid_rows")} valid, {errors} with errors
            {item.status !== "validated" ? ` · ${stat(item, "committed_rows")} written` : ""}
          </p>
        ) : null}
        {typeof message === "string" && message ? (
          <Alert variant="destructive">{message}</Alert>
        ) : null}
        {error ? <Alert variant="destructive">{error}</Alert> : null}
        <div className="flex gap-3">
          {item.has_error_file ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                void run(async () => {
                  const link = await call(
                    api().GET("/v1/imports/{import_id}/errors", {
                      params: { path: { import_id: item.id } },
                    }),
                  );
                  window.location.assign(link.url);
                })
              }
            >
              Download error file
            </Button>
          ) : null}
          {(item.status === "validated" && errors === 0) ||
          (item.status === "failed" && item.stats.phase === "commit") ? (
            <Button
              size="sm"
              onClick={() =>
                void run(() =>
                  call(
                    api().POST("/v1/imports/{import_id}/commit", {
                      params: { path: { import_id: item.id } },
                      body: { row_version: item.row_version },
                    }),
                  ),
                )
              }
            >
              {item.status === "failed" ? "Retry commit" : "Commit import"}
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

export default function ImportsPage() {
  const { data: session } = useSession();
  const queryClient = useQueryClient();
  const allowed = can(session, "platform.imports.run");
  const [typeKey, setTypeKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const types = useQuery({
    queryKey: ["admin", "imports", "types"],
    queryFn: () => call(api().GET("/v1/imports/types")),
    enabled: allowed,
  });
  const imports = useQuery({
    queryKey: ["admin", "imports", "list"],
    queryFn: () => call(api().GET("/v1/imports", { params: { query: { limit: 20 } } })),
    enabled: allowed,
    // Poll while the worker is busy with any of them.
    refetchInterval: (q) => (q.state.data?.items.some((i) => ACTIVE.has(i.status)) ? 1500 : false),
  });

  if (!allowed) return <NoAccess />;
  const available: ImportTypeOut[] = (types.data ?? []).filter((t) => t.permitted);
  const selected = available.find((t) => t.key === (typeKey || available[0]?.key));

  async function start(file: File) {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const fileId = await uploadAndScan(file);
      await call(
        api().POST("/v1/imports", {
          body: { import_type: selected.key, file_id: fileId },
          headers: { "Idempotency-Key": crypto.randomUUID() },
        }),
      );
      await queryClient.invalidateQueries({ queryKey: ["admin", "imports"] });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Imports</h1>
      <Card>
        <CardContent className="flex flex-col gap-4 pt-6">
          {available.length === 0 && !types.isLoading ? (
            <p className="text-sm text-muted-foreground" data-testid="no-import-types">
              Nothing can be imported yet. Import types arrive with the modules that use them.
            </p>
          ) : (
            <>
              <Field id="import-type" label="What are you importing?">
                <Select
                  id="import-type"
                  value={selected?.key ?? ""}
                  onChange={(e) => setTypeKey(e.target.value)}
                >
                  {available.map((t) => (
                    <option key={t.key} value={t.key}>
                      {t.label}
                    </option>
                  ))}
                </Select>
              </Field>
              {selected ? (
                <p className="text-sm text-muted-foreground" data-testid="import-columns">
                  Columns:{" "}
                  {selected.columns.map((c) => (c.required ? `${c.name}*` : c.name)).join(", ")}. A
                  header row is required; * marks required columns.
                </p>
              ) : null}
              <Field id="import-file" label="CSV or Excel file">
                <input
                  id="import-file"
                  type="file"
                  accept=".csv,.xlsx"
                  disabled={busy}
                  className="text-sm"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void start(file);
                    e.target.value = "";
                  }}
                />
              </Field>
              {busy ? <p className="text-sm">Uploading and scanning…</p> : null}
            </>
          )}
          {error ? <Alert variant="destructive">{error}</Alert> : null}
        </CardContent>
      </Card>
      {(imports.data?.items ?? []).map((item) => (
        <ImportCard key={item.id} item={item} />
      ))}
    </div>
  );
}
