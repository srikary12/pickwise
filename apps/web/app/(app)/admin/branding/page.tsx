// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import {
  Alert,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  FileUpload,
} from "@pickwise/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { NoAccess } from "@/components/no-access";
import { api, call, errorMessage } from "@/lib/api";
import { useApiMutation } from "@/lib/mutations";
import { can, SESSION_KEY, useSession } from "@/lib/session";
import { uploadAndScan } from "@/lib/upload";

const TENANT_KEY = ["admin", "tenant"] as const;
const MAX_BYTES = 1024 * 1024;

export default function BrandingPage() {
  const { data: session } = useSession();
  const allowed = can(session, "platform.tenant.manage");
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);

  const tenant = useQuery({
    queryKey: TENANT_KEY,
    queryFn: () => call(api().GET("/v1/admin/tenant")),
    enabled: allowed,
  });

  const changed = () => {
    void queryClient.invalidateQueries({ queryKey: TENANT_KEY });
    void queryClient.invalidateQueries({ queryKey: SESSION_KEY }); // carries logo_version
  };
  const setLogo = useApiMutation({
    mutationFn: (fileId: string) =>
      call(
        api().PUT("/v1/admin/tenant/logo", {
          body: { file_id: fileId, row_version: tenant.data?.row_version ?? 0 },
        }),
      ),
    onSuccess: () => {
      setMessage("Logo updated.");
      changed();
    },
  });
  const remove = useApiMutation({
    mutationFn: () =>
      call(
        api().DELETE("/v1/admin/tenant/logo", {
          params: { query: { row_version: tenant.data?.row_version ?? 0 } },
        }),
      ),
    onSuccess: () => {
      setMessage("Logo removed.");
      changed();
    },
  });

  if (!session) return null;
  if (!allowed) return <NoAccess />;
  const logo = session.logo_version;
  const error = setLogo.error ?? remove.error;
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Branding</h1>
      <Card>
        <CardHeader>
          <CardTitle>Logo</CardTitle>
          <CardDescription>
            Shown in the header for everyone in {session.active_tenant?.name ?? "your organisation"}
            . PNG or JPEG, up to 1 MB.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {logo ? (
            // eslint-disable-next-line @next/next/no-img-element -- a same-origin redirect to a signed URL
            <img
              src={`/api/v1/branding/logo?v=${logo}`}
              alt="Current logo"
              data-testid="logo-preview"
              className="h-16 w-auto max-w-64 self-start object-contain"
            />
          ) : (
            <p className="text-sm text-muted-foreground">
              No logo yet. The organisation name is shown.
            </p>
          )}
          {message ? <Alert variant="success">{message}</Alert> : null}
          {error ? <Alert variant="destructive">{errorMessage(error)}</Alert> : null}
          <FileUpload
            label="Choose a logo"
            accept="image/png,image/jpeg"
            maxBytes={MAX_BYTES}
            disabled={!tenant.data || !session.active_tenant}
            hint="PNG or JPEG, up to 1 MB."
            upload={(file, report, signal) =>
              uploadAndScan(file, {
                classification: "public",
                owner: { type: "tenant_logo", id: session.active_tenant?.tenant_id ?? "" },
                onStage: report,
                signal,
              })
            }
            onUploaded={(fileId) => {
              setMessage(null);
              setLogo.mutate(fileId);
            }}
          />
          {logo ? (
            <Button
              variant="outline"
              className="self-start"
              disabled={remove.isPending || !tenant.data}
              onClick={() => {
                setMessage(null);
                remove.mutate();
              }}
            >
              Remove logo
            </Button>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
