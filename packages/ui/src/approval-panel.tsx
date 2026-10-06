// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useState } from "react";

import { Alert } from "./alert";
import { Badge } from "./badge";
import { Button } from "./button";
import { Card, CardContent, CardHeader, CardTitle } from "./card";
import { formatDateTime } from "./format";
import { cn } from "./lib/utils";

export interface ApprovalTaskView {
  id: string;
  assignee: { name: string | null };
  status: string;
  acted_at: string | null;
  comment: string | null;
  delegated_from: { name: string | null } | null;
}

export interface ApprovalStepView {
  step_no: number;
  name: string;
  mode: "any" | "all";
  state: "done" | "current" | "upcoming";
  tasks: ApprovalTaskView[];
}

export interface ApprovalRequestView {
  policy_name: string;
  entity_type: string;
  status: string;
  requested_by: { name: string | null } | null;
  created_at: string;
  steps: ApprovalStepView[];
  /** The caller's pending task, if the request is waiting on them. */
  my_task_id: string | null;
  can_cancel: boolean;
}

export type ApprovalVerb = "approve" | "reject" | "cancel";

/**
 * An approval request: its steps and who has answered, plus the caller's actions. Presentational:
 * the page supplies `onDecide`, carries the request's `row_version`, and shows a conflict if the
 * request changed under the caller (HTTP 409).
 */
export function ApprovalPanel({
  request,
  onDecide,
  busy = false,
  error = null,
}: {
  request: ApprovalRequestView;
  onDecide: (verb: ApprovalVerb, comment: string | null) => void;
  busy?: boolean;
  error?: string | null;
}) {
  const [comment, setComment] = useState("");
  const canDecide = request.status === "pending" && request.my_task_id !== null;
  const decide = (verb: ApprovalVerb) => onDecide(verb, comment.trim() || null);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-xl font-semibold">{request.policy_name}</h2>
        <Badge variant="outline" data-testid="request-status">
          {request.status}
        </Badge>
      </div>
      <p className="text-sm text-muted-foreground">
        {request.entity_type.replaceAll("_", " ")}
        {request.requested_by?.name ? ` · requested by ${request.requested_by.name}` : ""} ·{" "}
        {formatDateTime(request.created_at)}
      </p>
      <ol className="flex flex-col gap-3" aria-label="Steps">
        {request.steps.map((step) => (
          <li key={step.step_no}>
            <Card className={cn(step.state === "current" && "border-primary")}>
              <CardHeader>
                <CardTitle className="flex flex-wrap items-center gap-2 text-base">
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
      {error ? <Alert variant="destructive">{error}</Alert> : null}
      {canDecide || request.can_cancel ? (
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
              className="min-h-20 rounded-md border border-border bg-card p-2 text-sm focus-visible:outline-2 focus-visible:outline-ring"
            />
            <div className="flex flex-wrap gap-2">
              {canDecide ? (
                <>
                  <Button data-testid="approve" disabled={busy} onClick={() => decide("approve")}>
                    Approve
                  </Button>
                  <Button
                    variant="destructive"
                    data-testid="reject"
                    disabled={busy}
                    onClick={() => decide("reject")}
                  >
                    Reject
                  </Button>
                </>
              ) : null}
              {request.can_cancel ? (
                <Button variant="outline" disabled={busy} onClick={() => decide("cancel")}>
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
