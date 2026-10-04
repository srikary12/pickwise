// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { PermissionOut } from "@pickwise/api-client";
import { Badge, Checkbox } from "@pickwise/ui";

/** A checklist of the permission catalog, grouped by module. */
export function PermissionPicker({
  catalog,
  selected,
  onChange,
  disabled,
}: {
  catalog: PermissionOut[];
  selected: ReadonlySet<string>;
  onChange: (next: Set<string>) => void;
  disabled?: boolean;
}) {
  const byModule = new Map<string, PermissionOut[]>();
  for (const permission of catalog) {
    byModule.set(permission.module, [...(byModule.get(permission.module) ?? []), permission]);
  }
  function toggle(code: string, on: boolean) {
    const next = new Set(selected);
    if (on) next.add(code);
    else next.delete(code);
    onChange(next);
  }
  return (
    <div className="flex max-h-72 flex-col gap-4 overflow-y-auto">
      {[...byModule.entries()].map(([module, permissions]) => (
        <fieldset key={module} className="flex flex-col gap-2">
          <legend className="mb-1 text-xs font-semibold uppercase text-muted-foreground">
            {module}
          </legend>
          {permissions.map((permission) => (
            <label key={permission.code} className="flex items-start gap-2 text-sm">
              <Checkbox
                checked={selected.has(permission.code)}
                disabled={disabled}
                onChange={(event) => toggle(permission.code, event.target.checked)}
                className="mt-0.5"
              />
              <span>
                <span className="font-mono text-xs">{permission.code}</span>
                {permission.is_sensitive ? (
                  <Badge variant="warning" className="ml-2">
                    sensitive
                  </Badge>
                ) : null}
                <span className="block text-muted-foreground">{permission.description}</span>
              </span>
            </label>
          ))}
        </fieldset>
      ))}
    </div>
  );
}
