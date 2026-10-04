// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { SessionState } from "@pickwise/api-client";
import {
  Alert,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@pickwise/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { api, call, errorMessage } from "@/lib/api";
import { routeForStage, SESSION_KEY, useSession } from "@/lib/session";

export default function SelectTenantPage() {
  const { data: session } = useSession();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  async function choose(tenantId: string) {
    try {
      const next = await call<SessionState>(
        api().POST("/v1/session/tenant", { body: { tenant_id: tenantId } }),
      );
      queryClient.clear();
      queryClient.setQueryData(SESSION_KEY, next);
      router.replace(routeForStage(next.stage));
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  if (!session) return null;
  return (
    <div className="mx-auto flex max-w-md flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Choose an organisation</CardTitle>
          <CardDescription>You belong to more than one. You can switch later.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {session.tenants.length === 0 ? (
            <Alert>You don&apos;t belong to an active organisation yet.</Alert>
          ) : (
            session.tenants.map((tenant) => (
              <Button
                key={tenant.tenant_id}
                variant="outline"
                onClick={() => void choose(tenant.tenant_id)}
              >
                {tenant.name}
              </Button>
            ))
          )}
          {error ? <Alert variant="destructive">{error}</Alert> : null}
        </CardContent>
      </Card>
    </div>
  );
}
