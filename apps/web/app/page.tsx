// SPDX-License-Identifier: AGPL-3.0-only
import { createApiClient, type Readiness } from "@pickwise/api-client";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@pickwise/ui";

// Always render at request time: this page reports live API health.
export const dynamic = "force-dynamic";

const API_URL = process.env.PICKWISE_API_INTERNAL_URL ?? "http://localhost:8000";

async function loadReadiness(): Promise<Readiness | null> {
  try {
    const api = createApiClient(API_URL);
    const { data, error } = await api.GET("/readyz", { cache: "no-store" });
    return data ?? error ?? null;
  } catch {
    return null;
  }
}

const CHECK_LABELS: Record<string, string> = {
  database: "Database",
  object_storage: "Object storage",
};

export default async function HealthPage() {
  const readiness = await loadReadiness();

  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-6 p-8">
      <h1 className="text-2xl font-semibold">Pickwise</h1>
      <Card>
        <CardHeader>
          <CardTitle>API health</CardTitle>
          <CardDescription>Live readiness of the backend and its dependencies.</CardDescription>
        </CardHeader>
        <CardContent>
          {readiness === null ? (
            <p data-testid="api-status">
              <Badge variant="destructive">unreachable</Badge> The API did not respond.
            </p>
          ) : (
            <div className="flex flex-col gap-4">
              <p data-testid="api-status" className="flex items-center gap-2">
                API status
                <Badge variant={readiness.status === "ok" ? "success" : "destructive"}>
                  {readiness.status}
                </Badge>
              </p>
              <ul className="flex flex-col gap-2">
                {Object.entries(readiness.checks).map(([name, check]) => (
                  <li
                    key={name}
                    className="flex items-center justify-between"
                    data-testid={`check-${name}`}
                  >
                    <span>{CHECK_LABELS[name] ?? name}</span>
                    <span className="flex items-center gap-2 text-sm text-muted-foreground">
                      {check.latency_ms} ms
                      <Badge variant={check.status === "ok" ? "success" : "destructive"}>
                        {check.status}
                      </Badge>
                    </span>
                  </li>
                ))}
              </ul>
              <p className="text-sm text-muted-foreground">
                Environment <code>{readiness.environment}</code> · scanner{" "}
                {readiness.scanner === "stub" ? (
                  <Badge variant="warning">STUB SCANNER — DEV ONLY</Badge>
                ) : (
                  <code>{readiness.scanner}</code>
                )}
              </p>
            </div>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
