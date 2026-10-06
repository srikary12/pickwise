// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import {
  Alert,
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  cn,
} from "@pickwise/ui";
import Link from "next/link";

import { errorMessage } from "@/lib/api";
import { safeLink, useDashboard } from "@/lib/dashboard";
import { formatDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";

export default function HomePage() {
  const { data: session } = useSession();
  const dashboard = useDashboard();
  if (!session) return null;
  const permissions = Object.entries(session.permissions ?? {}).sort(([a], [b]) =>
    a.localeCompare(b),
  );
  const data = dashboard.data;
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold" data-testid="welcome">
        {session.active_tenant ? `Welcome to ${session.active_tenant.name}` : "Welcome"}
      </h1>
      {dashboard.isError ? (
        <Alert variant="destructive">{errorMessage(dashboard.error)}</Alert>
      ) : null}
      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>
              Waiting for your decision{" "}
              {data ? (
                <Badge variant="outline" data-testid="pending-count">
                  {data.pending_approvals.count}
                </Badge>
              ) : null}
            </CardTitle>
            <CardDescription>Approvals assigned to you.</CardDescription>
          </CardHeader>
          <CardContent className="min-h-56">
            {!data ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : data.pending_approvals.items.length === 0 ? (
              <p className="text-sm text-muted-foreground">Nothing is waiting for you.</p>
            ) : (
              <ul className="flex flex-col divide-y divide-border">
                {data.pending_approvals.items.map((item) => (
                  <li key={item.task_id} className="py-2 text-sm">
                    <Link
                      href={`/approvals/${item.request_id}`}
                      className="font-medium underline-offset-4 hover:underline"
                    >
                      {item.policy_name}
                    </Link>
                    <p className="text-xs text-muted-foreground">
                      {item.requester_name ? `From ${item.requester_name} · ` : ""}
                      {formatDateTime(item.created_at)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
            {data && data.pending_approvals.count > data.pending_approvals.items.length ? (
              <p className="mt-3 text-sm">
                <Link href="/approvals" className="underline underline-offset-4">
                  See all {data.pending_approvals.count}
                </Link>
              </p>
            ) : null}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>
              Recent notifications{" "}
              {data && data.notifications.unread > 0 ? (
                <Badge variant="outline">{data.notifications.unread} unread</Badge>
              ) : null}
            </CardTitle>
            <CardDescription>The latest things that need your attention.</CardDescription>
          </CardHeader>
          <CardContent className="min-h-56">
            {!data ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : data.notifications.items.length === 0 ? (
              <p className="text-sm text-muted-foreground">No notifications yet.</p>
            ) : (
              <ul className="flex flex-col divide-y divide-border">
                {data.notifications.items.map((item) => {
                  const target = safeLink(item.link);
                  return (
                    <li key={item.id} className="py-2 text-sm">
                      <p className={cn(!item.read_at && "font-medium")}>
                        {target ? (
                          <Link href={target} className="underline-offset-4 hover:underline">
                            {item.title}
                          </Link>
                        ) : (
                          item.title
                        )}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {formatDateTime(item.created_at)}
                      </p>
                    </li>
                  );
                })}
              </ul>
            )}
            <p className="mt-3 text-sm">
              <Link href="/notifications" className="underline underline-offset-4">
                All notifications
              </Link>
            </p>
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Your access</CardTitle>
          <CardDescription>What your roles allow you to do in this organisation.</CardDescription>
        </CardHeader>
        <CardContent>
          {permissions.length === 0 ? (
            <p className="text-sm text-muted-foreground">No special permissions.</p>
          ) : (
            <ul className="flex flex-wrap gap-2">
              {permissions.map(([code, scopes]) => (
                <li key={code}>
                  <Badge variant="outline" title={`Scope: ${scopes.join(", ")}`}>
                    {code}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
