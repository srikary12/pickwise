// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { MemberOut, RoleOut } from "@pickwise/api-client";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  Dialog,
  Field,
  Input,
  Select,
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

const USERS = ["admin", "users"] as const;

const inviteSchema = z.object({
  email: z.email("Enter a valid email address."),
  display_name: z.string().trim().min(1, "Enter a name.").max(200),
  role_key: z.string().min(1),
});
type InviteValues = z.infer<typeof inviteSchema>;

function InviteDialog({
  open,
  onClose,
  roles,
}: {
  open: boolean;
  onClose: () => void;
  roles: RoleOut[];
}) {
  const queryClient = useQueryClient();
  // One key per opening of the dialog: a double submit or retry replays instead of inviting twice.
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());
  const form = useForm<InviteValues>({
    resolver: zodResolver(inviteSchema),
    defaultValues: { email: "", display_name: "", role_key: "employee" },
  });
  const { register, handleSubmit, setError, reset, formState } = form;

  return (
    <Dialog open={open} onClose={onClose} title="Invite a person">
      <form
        noValidate
        className="flex flex-col gap-4"
        onSubmit={handleSubmit(async (values) => {
          try {
            await call(
              api().POST("/v1/admin/users/invite", {
                body: values,
                headers: { "Idempotency-Key": idempotencyKey },
              }),
            );
            await queryClient.invalidateQueries({ queryKey: USERS });
            setIdempotencyKey(crypto.randomUUID());
            reset();
            onClose();
          } catch (error) {
            setError("root", { message: errorMessage(error) });
          }
        })}
      >
        <Field id="invite-email" label="Email" error={formState.errors.email?.message}>
          <Input id="invite-email" type="email" {...register("email")} />
        </Field>
        <Field id="invite-name" label="Name" error={formState.errors.display_name?.message}>
          <Input id="invite-name" {...register("display_name")} />
        </Field>
        <Field id="invite-role" label="Role">
          <Select id="invite-role" {...register("role_key")}>
            {roles.length === 0 ? <option value="employee">Employee</option> : null}
            {roles.map((role) => (
              <option key={role.id} value={role.key}>
                {role.name}
              </option>
            ))}
          </Select>
        </Field>
        {formState.errors.root ? (
          <Alert variant="destructive">{formState.errors.root.message}</Alert>
        ) : null}
        <div className="flex justify-end gap-3">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={formState.isSubmitting}>
            Send invitation
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

const STATUS_VARIANT = {
  active: "success",
  invited: "warning",
  suspended: "destructive",
} as const;

function MemberRow({
  member,
  roles,
  canManage,
  canManageRoles,
  isSelf,
  run,
}: {
  member: MemberOut;
  roles: RoleOut[];
  canManage: boolean;
  canManageRoles: boolean;
  isSelf: boolean;
  run: (action: () => Promise<unknown>) => Promise<void>;
}) {
  const [roleId, setRoleId] = useState("");
  const status = member.status as keyof typeof STATUS_VARIANT;
  const held = new Set(member.roles.map((r) => r.role_id));
  const grantable = roles.filter((r) => !held.has(r.id));

  return (
    <Tr data-testid={`member-${member.email}`}>
      <Td>
        <div className="font-medium">{member.display_name}</div>
        <div className="text-xs text-muted-foreground">{member.email}</div>
      </Td>
      <Td>
        <Badge variant={STATUS_VARIANT[status] ?? "outline"}>{member.status}</Badge>
        {member.mfa_enabled ? (
          <Badge variant="outline" className="ml-1">
            MFA
          </Badge>
        ) : null}
      </Td>
      <Td>
        <div className="flex flex-wrap items-center gap-1">
          {member.roles.map((assignment) => (
            <Badge key={assignment.id} variant="outline" className="gap-1">
              {assignment.role_key}
              {canManageRoles ? (
                <button
                  type="button"
                  aria-label={`Remove role ${assignment.role_key} from ${member.email}`}
                  className="text-muted-foreground hover:text-foreground"
                  onClick={() =>
                    void run(() =>
                      call(
                        api().DELETE("/v1/admin/role-assignments/{assignment_id}", {
                          params: { path: { assignment_id: assignment.id } },
                        }),
                      ),
                    )
                  }
                >
                  ×
                </button>
              ) : null}
            </Badge>
          ))}
        </div>
        {canManageRoles && grantable.length > 0 ? (
          <div className="mt-2 flex gap-2">
            <Select
              aria-label={`Add a role for ${member.email}`}
              className="h-8 w-40 text-xs"
              value={roleId}
              onChange={(event) => setRoleId(event.target.value)}
            >
              <option value="">Add role…</option>
              {grantable.map((role) => (
                <option key={role.id} value={role.id}>
                  {role.name}
                </option>
              ))}
            </Select>
            <Button
              size="sm"
              variant="outline"
              disabled={!roleId}
              onClick={() =>
                void run(async () => {
                  await call(
                    api().POST("/v1/admin/role-assignments", {
                      body: {
                        membership_id: member.membership_id,
                        role_id: roleId,
                        scope_type: "tenant",
                      },
                    }),
                  );
                  setRoleId("");
                })
              }
            >
              Add
            </Button>
          </div>
        ) : null}
      </Td>
      <Td className="text-right">
        {canManage && !isSelf && member.status !== "invited" ? (
          <Button
            size="sm"
            variant={member.status === "active" ? "outline" : "default"}
            onClick={() =>
              void run(() =>
                call(
                  api().PATCH("/v1/admin/users/{membership_id}", {
                    params: { path: { membership_id: member.membership_id } },
                    body: {
                      status: member.status === "active" ? "suspended" : "active",
                      row_version: member.row_version,
                    },
                  }),
                ),
              )
            }
          >
            {member.status === "active" ? "Suspend" : "Reactivate"}
          </Button>
        ) : null}
      </Td>
    </Tr>
  );
}

export default function UsersPage() {
  const queryClient = useQueryClient();
  const { data: session } = useSession();
  const allowed = can(session, "platform.users.read");
  const canRoles = can(session, "platform.roles.read");
  const [inviting, setInviting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const members = useQuery({
    queryKey: USERS,
    enabled: allowed,
    queryFn: () => call<MemberOut[]>(api().GET("/v1/admin/users")),
  });
  const roles = useQuery({
    queryKey: ["admin", "roles"],
    enabled: allowed && canRoles,
    queryFn: () => call<RoleOut[]>(api().GET("/v1/admin/roles")),
  });

  async function run(action: () => Promise<unknown>) {
    setNotice(null);
    try {
      await action();
    } catch (error) {
      setNotice(errorMessage(error));
    }
    // A stale row_version is fixed by reloading, so always refresh.
    await queryClient.invalidateQueries({ queryKey: USERS });
  }

  if (!session) return null;
  if (!allowed) return <NoAccess />;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Users</h1>
        {can(session, "platform.users.invite") ? (
          <Button onClick={() => setInviting(true)}>Invite a person</Button>
        ) : null}
      </div>
      {notice ? <Alert variant="destructive">{notice}</Alert> : null}
      {members.isError ? <Alert variant="destructive">{errorMessage(members.error)}</Alert> : null}
      <Card>
        <CardContent className="p-0">
          <Table>
            <THead>
              <Tr>
                <Th>Person</Th>
                <Th>Status</Th>
                <Th>Roles</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </Tr>
            </THead>
            <TBody>
              {(members.data ?? []).map((member) => (
                <MemberRow
                  key={member.membership_id}
                  member={member}
                  roles={roles.data ?? []}
                  canManage={can(session, "platform.users.manage")}
                  canManageRoles={can(session, "platform.roles.manage")}
                  isSelf={member.user_id === session.user.id}
                  run={run}
                />
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
      <InviteDialog open={inviting} onClose={() => setInviting(false)} roles={roles.data ?? []} />
    </div>
  );
}
