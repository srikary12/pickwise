// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { components } from "@pickwise/api-client";
import { Alert, ApprovalPanel, type ApprovalVerb } from "@pickwise/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";

import { api, call, errorMessage } from "@/lib/api";
import { DASHBOARD_KEY } from "@/lib/dashboard";
import { useApiMutation } from "@/lib/mutations";

type Request = components["schemas"]["RequestOut"];

export default function ApprovalPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const queryClient = useQueryClient();

  const request = useQuery({
    queryKey: ["approvals", "request", id],
    queryFn: () =>
      call<Request>(
        api().GET("/v1/approvals/{request_id}", { params: { path: { request_id: id } } }),
      ),
  });

  const decide = useApiMutation({
    mutationFn: ({
      verb,
      comment,
      version,
    }: {
      verb: ApprovalVerb;
      comment: string | null;
      version: number;
    }) =>
      call<Request>(
        api().POST(`/v1/approvals/{request_id}/${verb}` as "/v1/approvals/{request_id}/approve", {
          params: { path: { request_id: id } },
          body: { comment, row_version: version },
        }),
      ),
    onSuccess: (updated) => {
      queryClient.setQueryData(["approvals", "request", id], updated);
      void queryClient.invalidateQueries({ queryKey: ["approvals"] });
      void queryClient.invalidateQueries({ queryKey: DASHBOARD_KEY });
    },
  });

  if (request.isLoading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (request.isError || !request.data) {
    return <Alert variant="destructive">{errorMessage(request.error)}</Alert>;
  }
  const r = request.data;
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm">
        <Link href="/approvals" className="underline underline-offset-4">
          ← All approvals
        </Link>
      </p>
      <ApprovalPanel
        request={r}
        busy={decide.isPending}
        error={decide.isError ? errorMessage(decide.error) : null}
        onDecide={(verb, comment) => decide.mutate({ verb, comment, version: r.row_version })}
      />
    </div>
  );
}
