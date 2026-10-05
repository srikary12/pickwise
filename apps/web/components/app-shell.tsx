// SPDX-License-Identifier: AGPL-3.0-only
// A minimal shell: the real navigation arrives with the app shell in Phase 4.
"use client";

import { Button, cn, Select } from "@pickwise/ui";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect } from "react";

import { api, call } from "@/lib/api";
import { can, routeForStage, SESSION_KEY, useSession } from "@/lib/session";
import type { SessionState } from "@pickwise/api-client";

const NAV = [
  { href: "/", label: "Home", permission: null },
  { href: "/admin/users", label: "Users", permission: "platform.users.read" },
  { href: "/admin/roles", label: "Roles", permission: "platform.roles.read" },
  { href: "/admin/imports", label: "Imports", permission: "platform.imports.run" },
  { href: "/admin/webhooks", label: "Webhooks", permission: "platform.webhooks.manage" },
  { href: "/admin/audit", label: "Audit", permission: "platform.audit.read" },
  { href: "/settings/security", label: "Security", permission: null },
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const { data: session, isLoading } = useSession();

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

  if (isLoading || !session) {
    return <p className="p-8 text-sm text-muted-foreground">Loading…</p>;
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

  const ready = session.stage === "ready";
  return (
    <div className="min-h-screen">
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-4 px-6 py-3">
          <Link href="/" className="font-semibold">
            Pickwise
          </Link>
          {ready ? (
            <nav aria-label="Main" className="flex gap-4 text-sm">
              {NAV.filter((n) => !n.permission || can(session, n.permission)).map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    "underline-offset-4 hover:underline",
                    pathname === item.href && "font-medium underline",
                  )}
                >
                  {item.label}
                </Link>
              ))}
            </nav>
          ) : null}
          <div className="ml-auto flex items-center gap-3 text-sm">
            {session.tenants.length > 1 ? (
              <Select
                aria-label="Switch organisation"
                className="w-48"
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
              <span data-testid="tenant-name">{session.active_tenant.name}</span>
            ) : null}
            <span className="text-muted-foreground" data-testid="user-email">
              {session.user.email}
            </span>
            <Button variant="outline" size="sm" onClick={() => void signOut()}>
              Sign out
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl p-6">{children}</main>
    </div>
  );
}
