// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { components } from "@pickwise/api-client";
import { Alert, Badge, Button, Card, CardContent, CardHeader, CardTitle, cn } from "@pickwise/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use, useState } from "react";

import { api, call, errorMessage } from "@/lib/api";
import { DASHBOARD_KEY } from "@/lib/dashboard";
import { formatDateTime } from "@/lib/format";
import { useApiMutation } from "@/lib/mutations";

type Verb = "approve" | "reject" | "cancel";
type Request = components["schemas"]["RequestOut"];

export default function ApprovalPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const queryClient = useQueryClient();
  const [comment, setComment] = useState("");

  const request = useQuery({
    queryKey: ["approvals", "request", id],
    queryFn: () =>
      call<Request>(
        api().GET("/v1/approvals/{request_id}", { params: { path: { request_id: id } } }),
      ),
  });

  const decide = useApiMutation({
    mutationFn: ({ verb, version }: { verb: Verb; version: number }) =>
      call<Request>(
        api().POST(`/v1/approvals/{request_id}/${verb}` as "/v1/approvals/{request_id}/approve", {
          params: { path: { request_id: id } },
          body: { comment: comment.trim() || null, row_version: version },
        }),
      ),
    onSuccess: (updated) => {
      setComment("");
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
  const canDecide = r.status === "pending" && r.my_task_id !== null;
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm">
        <Link href="/approvals" className="underline underline-offset-4">
          ← All approvals
        </Link>
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">{r.policy_name}</h1>
        <Badge variant="outline" data-testid="request-status">
          {r.status}
        </Badge>
      </div>
      <p className="text-sm text-muted-foreground">
        {r.entity_type.replaceAll("_", " ")}
        {r.requested_by?.name ? ` · requested by ${r.requested_by.name}` : ""} ·{" "}
        {formatDateTime(r.created_at)}
      </p>
      <ol className="flex flex-col gap-3" aria-label="Steps">
        {r.steps.map((step) => (
          <li key={step.step_no}>
            <Card className={cn(step.state === "current" && "border-primary")}>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  {step.step_no}. {step.name}
                  <Badge variant="outline">
                    {step.state} · {step.mode === "all" ? "everyone" : "any one"}
                  </Badge>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="flex flex-col gap-1 text-sm">
                  {step.tasks.map((task) => (
                    <li key={task.id}>
                      <span className="font-medium">{task.assignee.name ?? "Someone"}</span>
                      {" — "}
                      {task.status}
                      {task.delegated_from?.name ? ` (for ${task.delegated_from.name})` : ""}
                      {task.acted_at ? ` · ${formatDateTime(task.acted_at)}` : ""}
                      {task.comment ? (
                        <span className="block text-muted-foreground">“{task.comment}”</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          </li>
        ))}
      </ol>
      {decide.isError ? <Alert variant="destructive">{errorMessage(decide.error)}</Alert> : null}
      {canDecide || r.can_cancel ? (
        <Card>
          <CardContent className="flex flex-col gap-3 pt-6">
            <label htmlFor="decision-comment" className="text-sm font-medium">
              Comment (optional)
            </label>
            <textarea
              id="decision-comment"
              value={comment}
              maxLength={2000}
              onChange={(event) => setComment(event.target.value)}
              className="min-h-20 rounded-md border border-border bg-card p-2 text-sm"
            />
            <div className="flex flex-wrap gap-2">
              {canDecide ? (
                <>
                  <Button
                    data-testid="approve"
                    disabled={decide.isPending}
                    onClick={() => decide.mutate({ verb: "approve", version: r.row_version })}
                  >
                    Approve
                  </Button>
                  <Button
                    variant="destructive"
                    data-testid="reject"
                    disabled={decide.isPending}
                    onClick={() => decide.mutate({ verb: "reject", version: r.row_version })}
                  >
                    Reject
                  </Button>
                </>
              ) : null}
              {r.can_cancel ? (
                <Button
                  variant="outline"
                  disabled={decide.isPending}
                  onClick={() => decide.mutate({ verb: "cancel", version: r.row_version })}
                >
                  Cancel request
                </Button>
              ) : null}
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
