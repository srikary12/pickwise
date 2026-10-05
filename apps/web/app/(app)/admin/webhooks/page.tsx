// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { DeliveryOut, EndpointOut } from "@pickwise/api-client";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  Dialog,
  Field,
  Input,
  Table,
  TBody,
  Td,
  Th,
  THead,
  Tr,
} from "@pickwise/ui";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { NoAccess } from "@/components/no-access";
import { api, call, errorMessage } from "@/lib/api";
import { can, useSession } from "@/lib/session";

const KEY = ["admin", "webhooks"] as const;

const schema = z.object({
  url: z.url("Enter a full URL, starting with https://."),
  event_types: z.string(),
});
type Values = z.infer<typeof schema>;

function CreateDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (secret: string) => void;
}) {
  const queryClient = useQueryClient();
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { url: "", event_types: "" },
  });
  const { register, handleSubmit, setError, reset, formState } = form;
  return (
    <Dialog open={open} onClose={onClose} title="Add a webhook endpoint">
      <form
        noValidate
        className="flex flex-col gap-4"
        onSubmit={handleSubmit(async (values) => {
          try {
            const created = await call(
              api().POST("/v1/webhooks/endpoints", {
                body: {
                  url: values.url,
                  event_types: values.event_types
                    .split(",")
                    .map((t) => t.trim())
                    .filter(Boolean),
                },
                headers: { "Idempotency-Key": idempotencyKey },
              }),
            );
            await queryClient.invalidateQueries({ queryKey: KEY });
            setIdempotencyKey(crypto.randomUUID());
            reset();
            onClose();
            if (created.secret) onCreated(created.secret);
          } catch (error) {
            setError("root", { message: errorMessage(error) });
          }
        })}
      >
        <Field id="hook-url" label="URL" error={formState.errors.url?.message}>
          <Input id="hook-url" placeholder="https://example.com/pickwise" {...register("url")} />
        </Field>
        <Field
          id="hook-types"
          label="Event types"
          hint="Comma-separated, e.g. leave.*, approval.approved. Leave empty for every event."
        >
          <Input id="hook-types" {...register("event_types")} />
        </Field>
        {formState.errors.root ? (
          <Alert variant="destructive">{formState.errors.root.message}</Alert>
        ) : null}
        <div className="flex justify-end gap-3">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={formState.isSubmitting}>
            Add endpoint
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

const STATUS_VARIANT = {
  succeeded: "success",
  pending: "warning",
  failed: "destructive",
  dead: "destructive",
} as const;

export default function WebhooksPage() {
  const { data: session } = useSession();
  const queryClient = useQueryClient();
  const allowed = can(session, "platform.webhooks.manage");
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const endpoints = useQuery({
    queryKey: [...KEY, "endpoints"],
    queryFn: () => call(api().GET("/v1/webhooks/endpoints")),
    enabled: allowed,
  });
  const deliveries = useQuery({
    queryKey: [...KEY, "deliveries"],
    queryFn: () => call(api().GET("/v1/webhooks/deliveries", { params: { query: { limit: 30 } } })),
    enabled: allowed,
    refetchInterval: 5000,
  });

  if (!allowed) return <NoAccess />;

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      await queryClient.invalidateQueries({ queryKey: KEY });
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  const byId = new Map<string, EndpointOut>((endpoints.data ?? []).map((e) => [e.id, e]));
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Webhooks</h1>
        <Button onClick={() => setCreating(true)}>Add endpoint</Button>
      </div>
      {secret ? (
        <Alert data-testid="new-secret">
          <p className="font-medium">Copy the signing secret now. It isn&apos;t shown again.</p>
          <code className="mt-2 block break-all text-sm" data-testid="secret-value">
            {secret}
          </code>
          <Button variant="outline" size="sm" className="mt-3" onClick={() => setSecret(null)}>
            I&apos;ve saved it
          </Button>
        </Alert>
      ) : null}
      {error ? <Alert variant="destructive">{error}</Alert> : null}
      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead>
              <Tr>
                <Th>URL</Th>
                <Th>Events</Th>
                <Th>Status</Th>
                <Th />
              </Tr>
            </THead>
            <TBody>
              {(endpoints.data ?? []).map((e) => (
                <Tr key={e.id} data-testid={`endpoint-${e.url}`}>
                  <Td className="break-all">{e.url}</Td>
                  <Td className="text-xs">
                    {e.event_types.length ? e.event_types.join(", ") : "all"}
                  </Td>
                  <Td>
                    <Badge variant={e.is_active ? "success" : "outline"}>
                      {e.is_active ? "active" : "disabled"}
                    </Badge>
                  </Td>
                  <Td className="flex justify-end gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        void run(() =>
                          call(
                            api().PUT("/v1/webhooks/endpoints/{endpoint_id}", {
                              params: { path: { endpoint_id: e.id } },
                              body: {
                                url: e.url,
                                event_types: e.event_types,
                                is_active: !e.is_active,
                                row_version: e.row_version,
                              },
                            }),
                          ),
                        )
                      }
                    >
                      {e.is_active ? "Disable" : "Enable"}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!e.is_active}
                      onClick={() =>
                        void run(() =>
                          call(
                            api().POST("/v1/webhooks/endpoints/{endpoint_id}/test", {
                              params: { path: { endpoint_id: e.id } },
                            }),
                          ),
                        )
                      }
                    >
                      Send test
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        void run(async () => {
                          const rotated = await call(
                            api().POST("/v1/webhooks/endpoints/{endpoint_id}/rotate-secret", {
                              params: { path: { endpoint_id: e.id } },
                            }),
                          );
                          setSecret(rotated.secret);
                        })
                      }
                    >
                      Rotate secret
                    </Button>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
          {endpoints.data?.length === 0 ? (
            <p className="mt-3 text-sm text-muted-foreground">No endpoints yet.</p>
          ) : null}
        </CardContent>
      </Card>

      <h2 className="text-lg font-semibold">Recent deliveries</h2>
      <Table>
        <THead>
          <Tr>
            <Th>Event</Th>
            <Th>Endpoint</Th>
            <Th>Status</Th>
            <Th>Attempts</Th>
            <Th>Detail</Th>
            <Th />
          </Tr>
        </THead>
        <TBody>
          {(deliveries.data?.items ?? []).map((d: DeliveryOut) => (
            <Tr key={d.id} data-testid="delivery-row">
              <Td className="font-mono text-xs">{d.event_type}</Td>
              <Td className="break-all text-xs">{byId.get(d.endpoint_id)?.url ?? d.endpoint_id}</Td>
              <Td>
                <Badge variant={STATUS_VARIANT[d.status] ?? "outline"}>{d.status}</Badge>
              </Td>
              <Td>{d.attempt}</Td>
              <Td className="text-xs">
                {d.last_error ?? (d.response_code ? `HTTP ${d.response_code}` : "")}
              </Td>
              <Td>
                {d.status !== "pending" ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      void run(() =>
                        call(
                          api().POST("/v1/webhooks/deliveries/{delivery_id}/replay", {
                            params: { path: { delivery_id: d.id } },
                          }),
                        ),
                      )
                    }
                  >
                    Replay
                  </Button>
                ) : null}
              </Td>
            </Tr>
          ))}
        </TBody>
      </Table>
      <CreateDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(value) => setSecret(value)}
      />
    </div>
  );
}
