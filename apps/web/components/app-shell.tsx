// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { Button, cn, Select } from "@pickwise/ui";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { CommandPalette } from "@/components/command-palette";
import { MenuIcon, SearchIcon } from "@/components/icons";
import { NotificationBell } from "@/components/notification-bell";
import { ThemeToggle } from "@/components/theme-toggle";
import { api, call } from "@/lib/api";
import { useDashboard } from "@/lib/dashboard";
import { NAV } from "@/lib/nav";
import { can, routeForStage, SESSION_KEY, useSession } from "@/lib/session";
import type { SessionState } from "@pickwise/api-client";

function Brand({ session }: { session: SessionState }) {
  const name = session.active_tenant?.name ?? "Pickwise";
  return (
    <Link href="/" className="flex items-center gap-2 font-semibold">
      {session.logo_version ? (
        // eslint-disable-next-line @next/next/no-img-element -- a same-origin redirect to a signed URL
        <img
          src={`/api/v1/branding/logo?v=${session.logo_version}`}
          alt=""
          data-testid="tenant-logo"
          className="h-7 w-auto max-w-32 object-contain"
        />
      ) : null}
      <span className={session.logo_version ? "sr-only" : undefined}>{name}</span>
    </Link>
  );
}

/** The shell's frame while the session loads, so the page doesn't jump when it arrives. */
function ShellSkeleton() {
  return (
    <div className="min-h-screen">
      <header className="flex min-h-[53px] items-center border-b border-border bg-card px-4 font-semibold">
        Pickwise
      </header>
      <div className="md:flex">
        <aside className="hidden border-border bg-card md:block md:h-[calc(100vh-53px)] md:w-60 md:shrink-0 md:border-r" />
        <main className="mx-auto w-full max-w-5xl p-4 sm:p-6">
          <p role="status" className="sr-only">
            Loading…
          </p>
          <div aria-hidden="true" className="h-8 w-64 rounded bg-muted" />
        </main>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const { data: session, isLoading } = useSession();
  // The mobile menu is open for one page only: navigating (a new pathname) closes it.
  const [menuAt, setMenuAt] = useState<string | null>(null);
  const menuOpen = menuAt === pathname;
  const setMenuOpen = (open: boolean) => setMenuAt(open ? pathname : null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const main = useRef<HTMLElement>(null);
  const firstPath = useRef(pathname);
  const ready = session?.stage === "ready";
  // Fetched alongside the session, not after it: one round trip less before the page is useful.
  const counts = useDashboard().data;

  // Route by the session's stage; a stage other than `ready` confines the user to one page.
  useEffect(() => {
    if (isLoading) return;
    if (!session) {
      router.replace("/login");
      return;
    }
    const target = routeForStage(session.stage).split("?")[0] ?? "/";
    const allowed = session.stage === "ready" ? true : pathname === target;
    if (!allowed) router.replace(routeForStage(session.stage));
  }, [isLoading, session, pathname, router]);

  // After a client-side navigation, move focus to the page so keyboard and screen-reader
  // users start at its top instead of wherever the clicked link was.
  useEffect(() => {
    if (pathname !== firstPath.current) main.current?.focus();
  }, [pathname]);

  // Ctrl/Cmd+K opens search from anywhere.
  useEffect(() => {
    if (!ready) return;
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen(true);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [ready]);

  useEffect(() => {
    if (!menuOpen) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setMenuAt(null);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  if (isLoading || !session) {
    return <ShellSkeleton />;
  }
  if (session.stage !== "ready" && pathname !== routeForStage(session.stage).split("?")[0]) {
    return null;
  }

  async function switchTenant(tenantId: string) {
    const next = await call<SessionState>(
      api().POST("/v1/session/tenant", { body: { tenant_id: tenantId } }),
    );
    // Everything cached belonged to the previous organisation.
    queryClient.clear();
    queryClient.setQueryData(SESSION_KEY, next);
    router.replace(routeForStage(next.stage));
    router.refresh();
  }

  async function signOut() {
    await call(api().POST("/v1/auth/logout"));
    queryClient.clear();
    router.replace("/login");
  }

  const badge = (kind: "approvals" | "notifications" | undefined): number =>
    kind === "approvals"
      ? (counts?.pending_approvals.count ?? 0)
      : kind === "notifications"
        ? (counts?.notifications.unread ?? 0)
        : 0;

  const sidebar = (
    <nav aria-label="Main" className="flex flex-col gap-5 p-4 text-sm">
      {NAV.map((group) => {
        const items = group.items.filter((n) => !n.permission || can(session, n.permission));
        if (items.length === 0) return null;
        return (
          <div key={group.label} className="flex flex-col gap-1">
            <p className="px-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {group.label}
            </p>
            {items.map((item) => {
              const count = badge(item.badge);
              const current = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={current ? "page" : undefined}
                  className={cn(
                    "flex items-center justify-between rounded-md px-2 py-1.5 hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring",
                    current && "bg-muted font-medium",
                  )}
                >
                  <span>{item.label}</span>
                  {count > 0 ? (
                    <span className="rounded-full bg-primary px-1.5 text-xs text-primary-foreground">
                      <span className="sr-only">, </span>
                      {count > 99 ? "99+" : count}
                    </span>
                  ) : null}
                </Link>
              );
            })}
          </div>
        );
      })}
    </nav>
  );

  return (
    <div className="min-h-screen">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-card focus:px-3 focus:py-2 focus:shadow"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-20 border-b border-border bg-card">
        <div className="flex items-center gap-2 px-4 py-2">
          {ready ? (
            <Button
              variant="ghost"
              size="icon"
              className="md:hidden"
              aria-label="Menu"
              aria-expanded={menuOpen}
              aria-controls="sidebar"
              onClick={() => setMenuOpen(!menuOpen)}
            >
              <MenuIcon />
            </Button>
          ) : null}
          <Brand session={session} />
          <div className="ml-auto flex items-center gap-1 text-sm sm:gap-2">
            {ready ? (
              <Button
                variant="outline"
                size="sm"
                className="gap-2"
                data-testid="search-open"
                aria-keyshortcuts="Control+K Meta+K"
                onClick={() => setPaletteOpen(true)}
              >
                <SearchIcon />
                <span className="hidden sm:inline">Search</span>
                <kbd className="hidden rounded border border-border px-1 text-[10px] text-muted-foreground sm:inline">
                  Ctrl K
                </kbd>
              </Button>
            ) : null}
            {session.tenants.length > 1 ? (
              <Select
                aria-label="Switch organisation"
                className="w-40 sm:w-48"
                value={session.active_tenant?.tenant_id ?? ""}
                onChange={(event) => void switchTenant(event.target.value)}
              >
                {session.active_tenant ? null : <option value="">Choose…</option>}
                {session.tenants.map((t) => (
                  <option key={t.tenant_id} value={t.tenant_id}>
                    {t.name}
                  </option>
                ))}
              </Select>
            ) : session.active_tenant ? (
              <span data-testid="tenant-name" className="hidden sm:inline">
                {session.active_tenant.name}
              </span>
            ) : null}
            {ready ? <NotificationBell /> : null}
            <ThemeToggle />
            <span className="hidden text-muted-foreground lg:inline" data-testid="user-email">
              {session.user.email}
            </span>
            <Button variant="outline" size="sm" onClick={() => void signOut()}>
              Sign out
            </Button>
          </div>
        </div>
      </header>
      <div className="md:flex">
        {ready ? (
          <>
            <aside
              id="sidebar"
              className={cn(
                "border-border bg-card md:sticky md:top-[53px] md:block md:h-[calc(100vh-53px)] md:w-60 md:shrink-0 md:overflow-y-auto md:border-r",
                menuOpen ? "block border-b" : "hidden",
              )}
            >
              {sidebar}
            </aside>
          </>
        ) : null}
        <main
          id="main"
          ref={main}
          tabIndex={-1}
          className="mx-auto w-full max-w-5xl p-4 outline-none sm:p-6"
        >
          {children}
        </main>
      </div>
      {ready ? (
        <CommandPalette
          session={session}
          open={paletteOpen}
          onClose={() => setPaletteOpen(false)}
        />
      ) : null}
    </div>
  );
}
