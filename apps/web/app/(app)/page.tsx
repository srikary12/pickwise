// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@pickwise/ui";

import { useSession } from "@/lib/session";

export default function HomePage() {
  const { data: session } = useSession();
  if (!session) return null;
  const permissions = Object.entries(session.permissions ?? {}).sort(([a], [b]) =>
    a.localeCompare(b),
  );
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold" data-testid="welcome">
        {session.active_tenant ? `Welcome to ${session.active_tenant.name}` : "Welcome"}
      </h1>
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
