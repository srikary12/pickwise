// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { NotificationOut } from "@pickwise/api-client";
import { Button, cn } from "@pickwise/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { BellIcon } from "@/components/icons";
import { api, call } from "@/lib/api";
import { DASHBOARD_KEY, safeLink, useDashboard } from "@/lib/dashboard";
import { useApiMutation } from "@/lib/mutations";

export const NOTIFICATIONS_KEY = ["notifications"] as const;

/** The unread count in the header, and a popover with the latest notifications. */
export function NotificationBell() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const unread = useDashboard().data?.notifications.unread ?? 0;

  const latest = useQuery({
    queryKey: [...NOTIFICATIONS_KEY, "latest"],
    queryFn: () => call(api().GET("/v1/notifications", { params: { query: { limit: 8 } } })),
    enabled: open,
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

  // Close on Escape (returning focus to the button) and on a click elsewhere.
  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOpen(false);
      root.current?.querySelector<HTMLButtonElement>("button[aria-haspopup]")?.focus();
    }
    function onClick(event: MouseEvent) {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  function openNotification(item: NotificationOut) {
    if (!item.read_at) markRead.mutate(item.id);
    setOpen(false);
    const target = safeLink(item.link);
    if (target) router.push(target);
  }

  return (
    <div ref={root} className="relative">
      <Button
        variant="ghost"
        size="icon"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="notification-panel"
        aria-label={unread > 0 ? `Notifications, ${unread} unread` : "Notifications"}
        data-testid="bell"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="relative">
          <BellIcon />
          {unread > 0 ? (
            <span
              data-testid="bell-count"
              aria-hidden="true"
              className="absolute -right-2 -top-2 min-w-4 rounded-full bg-destructive px-1 text-center text-[10px] font-semibold leading-4 text-destructive-foreground"
            >
              {unread > 99 ? "99+" : unread}
            </span>
          ) : null}
        </span>
      </Button>
      {open ? (
        <div
          id="notification-panel"
          role="dialog"
          aria-label="Notifications"
          className="absolute right-0 z-30 mt-2 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-border bg-card text-card-foreground shadow-lg"
        >
          <div className="flex items-center justify-between border-b border-border px-3 py-2">
            <h2 className="text-sm font-semibold">Notifications</h2>
            <Button
              variant="link"
              size="sm"
              disabled={unread === 0 || markAll.isPending}
              onClick={() => markAll.mutate()}
            >
              Mark all read
            </Button>
          </div>
          {latest.isLoading ? (
            <p className="p-4 text-sm text-muted-foreground">Loading…</p>
          ) : latest.data && latest.data.items.length > 0 ? (
            <ul className="max-h-96 overflow-y-auto">
              {latest.data.items.map((item) => (
                <li key={item.id} className="border-b border-border last:border-0">
                  <button
                    type="button"
                    onClick={() => openNotification(item)}
                    className={cn(
                      "flex w-full flex-col gap-0.5 px-3 py-2 text-left text-sm hover:bg-muted focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring",
                      !item.read_at && "font-medium",
                    )}
                  >
                    <span>
                      {!item.read_at ? <span className="sr-only">Unread: </span> : null}
                      {item.title}
                    </span>
                    {item.body ? (
                      <span className="line-clamp-2 text-xs font-normal text-muted-foreground">
                        {item.body}
                      </span>
                    ) : null}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="p-4 text-sm text-muted-foreground">You&apos;re all caught up.</p>
          )}
          <div className="border-t border-border px-3 py-2 text-right text-sm">
            <Link
              href="/notifications"
              className="underline underline-offset-4"
              onClick={() => setOpen(false)}
            >
              View all
            </Link>
          </div>
        </div>
      ) : null}
    </div>
  );
}
