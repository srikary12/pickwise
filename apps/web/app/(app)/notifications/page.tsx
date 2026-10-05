// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { NotificationOut } from "@pickwise/api-client";
import { Alert, Button, Card, CardContent, cn } from "@pickwise/ui";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { NOTIFICATIONS_KEY } from "@/components/notification-bell";
import { api, call, errorMessage } from "@/lib/api";
import { DASHBOARD_KEY, safeLink } from "@/lib/dashboard";
import { formatDateTime } from "@/lib/format";
import { useApiMutation } from "@/lib/mutations";

export default function NotificationsPage() {
  const queryClient = useQueryClient();
  const [unreadOnly, setUnreadOnly] = useState(false);

  const list = useInfiniteQuery({
    queryKey: [...NOTIFICATIONS_KEY, "page", unreadOnly],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      call(
        api().GET("/v1/notifications", {
          params: {
            query: { unread: unreadOnly, limit: 30, ...(pageParam ? { before: pageParam } : {}) },
          },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_KEY });
    void queryClient.invalidateQueries({ queryKey: DASHBOARD_KEY });
  };
  const markRead = useApiMutation({
    mutationFn: (id: string) =>
      call(
        api().POST("/v1/notifications/{notification_id}/read", {
          params: { path: { notification_id: id } },
        }),
      ),
    onSuccess: refresh,
  });
  const markAll = useApiMutation({
    mutationFn: () => call(api().POST("/v1/notifications/read-all")),
    onSuccess: refresh,
  });

  const items: NotificationOut[] = list.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-semibold">Notifications</h1>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={unreadOnly}
              onChange={(event) => setUnreadOnly(event.target.checked)}
            />
            Unread only
          </label>
          <Button
            variant="outline"
            size="sm"
            disabled={markAll.isPending}
            onClick={() => markAll.mutate()}
          >
            Mark all read
          </Button>
        </div>
      </div>
      {list.isError ? <Alert variant="destructive">{errorMessage(list.error)}</Alert> : null}
      <Card>
        <CardContent className="p-0">
          {list.isLoading ? (
            <p className="p-4 text-sm text-muted-foreground">Loading…</p>
          ) : items.length === 0 ? (
            <p className="p-4 text-sm text-muted-foreground" data-testid="notifications-empty">
              {unreadOnly ? "No unread notifications." : "No notifications yet."}
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {items.map((item) => {
                const target = safeLink(item.link);
                return (
                  <li
                    key={item.id}
                    data-testid="notification"
                    className="flex items-start justify-between gap-4 p-4 text-sm"
                  >
                    <div className="flex flex-col gap-0.5">
                      <p className={cn(!item.read_at && "font-semibold")}>
                        {!item.read_at ? <span className="sr-only">Unread: </span> : null}
                        {target ? (
                          <Link
                            href={target}
                            className="underline-offset-4 hover:underline"
                            onClick={() => !item.read_at && markRead.mutate(item.id)}
                          >
                            {item.title}
                          </Link>
                        ) : (
                          item.title
                        )}
                      </p>
                      {item.body ? <p className="text-muted-foreground">{item.body}</p> : null}
                      <p className="text-xs text-muted-foreground">
                        {formatDateTime(item.created_at)}
                      </p>
                    </div>
                    {!item.read_at ? (
                      <Button variant="ghost" size="sm" onClick={() => markRead.mutate(item.id)}>
                        Mark read
                      </Button>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>
      {list.hasNextPage ? (
        <div className="text-center">
          <Button
            variant="outline"
            disabled={list.isFetchingNextPage}
            onClick={() => void list.fetchNextPage()}
          >
            {list.isFetchingNextPage ? "Loading…" : "Load more"}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
