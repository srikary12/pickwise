// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { PermissionOut, RoleOut } from "@pickwise/api-client";
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
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { NoAccess } from "@/components/no-access";
import { PermissionPicker } from "@/components/permission-picker";
import { api, call, errorMessage } from "@/lib/api";
import { can, useSession } from "@/lib/session";

const ROLES = ["admin", "roles"] as const;

const createSchema = z.object({
  key: z.string().regex(/^[a-z][a-z0-9_]{1,62}$/, "Lowercase letters, digits and underscores."),
  name: z.string().trim().min(1, "Enter a name.").max(200),
  description: z.string().max(1000),
});
type CreateValues = z.infer<typeof createSchema>;

function CreateRoleDialog({
  open,
  onClose,
  catalog,
}: {
  open: boolean;
  onClose: () => void;
  catalog: PermissionOut[];
}) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const form = useForm<CreateValues>({
    resolver: zodResolver(createSchema),
    defaultValues: { key: "", name: "", description: "" },
  });
  const { register, handleSubmit, setError, reset, formState } = form;

  return (
    <Dialog open={open} onClose={onClose} title="New role" className="max-w-lg">
      <form
        noValidate
        className="flex flex-col gap-4"
        onSubmit={handleSubmit(async (values) => {
          try {
            await call(
              api().POST("/v1/admin/roles", {
                body: {
                  key: values.key,
                  name: values.name,
                  description: values.description || null,
                  permissions: [...selected],
                },
              }),
            );
            await queryClient.invalidateQueries({ queryKey: ROLES });
            reset();
            setSelected(new Set());
            onClose();
          } catch (error) {
            setError("root", { message: errorMessage(error) });
          }
        })}
      >
        <Field id="role-name" label="Name" error={formState.errors.name?.message}>
          <Input id="role-name" {...register("name")} />
        </Field>
        <Field
          id="role-key"
          label="Key"
          hint="Used in integrations; can't be changed."
          error={formState.errors.key?.message}
        >
          <Input id="role-key" {...register("key")} />
        </Field>
        <Field
          id="role-description"
          label="Description"
          error={formState.errors.description?.message}
        >
          <Input id="role-description" {...register("description")} />
        </Field>
        <PermissionPicker catalog={catalog} selected={selected} onChange={setSelected} />
        {formState.errors.root ? (
          <Alert variant="destructive">{formState.errors.root.message}</Alert>
        ) : null}
        <div className="flex justify-end gap-3">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={formState.isSubmitting}>
            Create role
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function EditPermissionsDialog({
  role,
  catalog,
  onClose,
}: {
  role: RoleOut | null;
  catalog: PermissionOut[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set(role?.permissions ?? []));
  const save = useMutation({
    mutationFn: () =>
      call(
        api().PUT("/v1/admin/roles/{role_id}/permissions", {
          params: { path: { role_id: role?.id ?? "" } },
          body: { permissions: [...selected], row_version: role?.row_version ?? 0 },
        }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ROLES });
      onClose();
    },
  });
  return (
    <Dialog
      open={role !== null}
      onClose={onClose}
      title={role ? `Permissions for ${role.name}` : "Permissions"}
      className="max-w-lg"
    >
      <div className="flex flex-col gap-4">
        <PermissionPicker catalog={catalog} selected={selected} onChange={setSelected} />
        {save.isError ? <Alert variant="destructive">{errorMessage(save.error)}</Alert> : null}
        <div className="flex justify-end gap-3">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={save.isPending} onClick={() => save.mutate()}>
            Save
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

export default function RolesPage() {
  const { data: session } = useSession();
  const allowed = can(session, "platform.roles.read");
  const canManage = can(session, "platform.roles.manage");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<RoleOut | null>(null);

  const roles = useQuery({
    queryKey: ROLES,
    enabled: allowed,
    queryFn: () => call<RoleOut[]>(api().GET("/v1/admin/roles")),
  });
  const catalog = useQuery({
    queryKey: ["permissions"],
    enabled: allowed,
    queryFn: () => call<PermissionOut[]>(api().GET("/v1/permissions")),
  });

  if (!session) return null;
  if (!allowed) return <NoAccess />;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Roles</h1>
        {canManage ? <Button onClick={() => setCreating(true)}>New role</Button> : null}
      </div>
      {roles.isError ? <Alert variant="destructive">{errorMessage(roles.error)}</Alert> : null}
      <Card>
        <CardContent className="p-0">
          <Table>
            <THead>
              <Tr>
                <Th>Role</Th>
                <Th>Permissions</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </Tr>
            </THead>
            <TBody>
              {(roles.data ?? []).map((role) => (
                <Tr key={role.id} data-testid={`role-${role.key}`}>
                  <Td>
                    <div className="font-medium">{role.name}</div>
                    <div className="font-mono text-xs text-muted-foreground">{role.key}</div>
                  </Td>
                  <Td>
                    {role.is_system ? <Badge variant="outline">System</Badge> : null}{" "}
                    {role.permissions.length} permission{role.permissions.length === 1 ? "" : "s"}
                  </Td>
                  <Td className="text-right">
                    {canManage && !role.is_system ? (
                      <Button variant="outline" size="sm" onClick={() => setEditing(role)}>
                        Edit permissions
                      </Button>
                    ) : null}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
      <CreateRoleDialog
        open={creating}
        onClose={() => setCreating(false)}
        catalog={catalog.data ?? []}
      />
      {editing ? (
        <EditPermissionsDialog
          key={editing.id}
          role={editing}
          catalog={catalog.data ?? []}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </div>
  );
}
