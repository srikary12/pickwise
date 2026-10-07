// SPDX-License-Identifier: AGPL-3.0-only
"use client";

// Emergency contacts, dependents and nominations (the share of PF, EPS, EDLI, gratuity and
// insurance each dependent gets; each scheme must total 100).
import type { NominationOut } from "@pickwise/api-client";
import { Alert, Button } from "@pickwise/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ResourcePanel } from "@/components/org/resource-panel";
import { dependents, emergencyContacts, type DependentRow } from "@/components/people/resources";
import { api, call, errorMessage } from "@/lib/api";
import { showConflict } from "@/lib/conflict";
import { isStaleVersion } from "@/lib/query";

const SCHEMES = [
  { value: "pf", label: "Provident fund" },
  { value: "eps", label: "Pension (EPS)" },
  { value: "edli", label: "EDLI" },
  { value: "gratuity", label: "Gratuity" },
  { value: "insurance", label: "Insurance" },
] as const;
type Scheme = (typeof SCHEMES)[number]["value"];

function SchemeEditor({
  employeeId,
  scheme,
  label,
  people,
  saved,
}: {
  employeeId: string;
  scheme: Scheme;
  label: string;
  people: DependentRow[];
  saved: NominationOut[];
}) {
  const queryClient = useQueryClient();
  const [shares, setShares] = useState<Record<string, string>>(() =>
    Object.fromEntries(saved.map((n) => [n.dependent_id, String(n.share_percent)])),
  );
  const [error, setError] = useState<string | null>(null);
  const total = Object.values(shares).reduce((sum, v) => sum + (Number(v) || 0), 0);
  const filled = Object.entries(shares).filter(([, v]) => v !== "" && Number(v) > 0);

  async function save() {
    setError(null);
    try {
      await call(
        api().PUT("/v1/employees/{employee_id}/nominations/{scheme}", {
          params: { path: { employee_id: employeeId, scheme } },
          body: {
            shares: filled.map(([dependent_id, share]) => ({
              dependent_id,
              share_percent: share,
            })),
          },
        }),
      );
      await queryClient.invalidateQueries({ queryKey: ["employee", employeeId, "nominations"] });
    } catch (caught) {
      if (isStaleVersion(caught)) showConflict();
      else setError(errorMessage(caught));
    }
  }

  return (
    <fieldset
      className="rounded-lg border border-border bg-card p-4"
      data-testid={`nominations-${scheme}`}
    >
      <legend className="px-1 font-medium">{label}</legend>
      <div className="flex flex-col gap-2">
        {people.map((p) => (
          <label key={p.id} className="flex items-center justify-between gap-3 text-sm">
            <span>{p.name}</span>
            <span className="flex items-center gap-1">
              <input
                aria-label={`${label} share for ${p.name} (percent)`}
                inputMode="decimal"
                className="h-8 w-20 rounded-md border border-border bg-card px-2 text-right tabular-nums"
                value={shares[p.id] ?? ""}
                onChange={(event) => setShares({ ...shares, [p.id]: event.target.value })}
              />
              %
            </span>
          </label>
        ))}
      </div>
      <div className="mt-3 flex items-center justify-between text-sm">
        <span
          className={
            total === 100 || filled.length === 0 ? "text-muted-foreground" : "text-destructive"
          }
          aria-live="polite"
        >
          Total {total}%{filled.length > 0 && total !== 100 ? " — must be 100" : ""}
        </span>
        <Button size="sm" disabled={filled.length > 0 && total !== 100} onClick={() => void save()}>
          Save {label}
        </Button>
      </div>
      {error ? <Alert variant="destructive">{error}</Alert> : null}
    </fieldset>
  );
}

function Nominations({ employeeId, canEdit }: { employeeId: string; canEdit: boolean }) {
  const people = useQuery({
    queryKey: ["org", `dependents-${employeeId}`, { archived: false }],
    queryFn: () =>
      call<DependentRow[]>(
        api().GET("/v1/employees/{employee_id}/dependents", {
          params: { path: { employee_id: employeeId } },
        }),
      ),
  });
  const nominations = useQuery({
    queryKey: ["employee", employeeId, "nominations"],
    queryFn: () =>
      call<NominationOut[]>(
        api().GET("/v1/employees/{employee_id}/nominations", {
          params: { path: { employee_id: employeeId } },
        }),
      ),
  });
  if (!people.data || !nominations.data) return null;
  if (people.data.length === 0) {
    return <p className="text-sm text-muted-foreground">Add a dependent to nominate them.</p>;
  }
  const version = JSON.stringify(nominations.data) + people.data.map((p) => p.id).join();
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {SCHEMES.map((s) => (
        <SchemeEditor
          key={`${s.value}-${version}`}
          employeeId={employeeId}
          scheme={s.value}
          label={s.label}
          people={people.data}
          saved={nominations.data.filter((n) => n.scheme === s.value)}
        />
      ))}
      {canEdit ? null : null}
    </div>
  );
}

export function FamilyTab({
  employeeId,
  canEdit,
  self = false,
}: {
  employeeId: string;
  canEdit: boolean;
  self?: boolean;
}) {
  return (
    <div className="flex flex-col gap-8" data-testid="family-tab">
      <section aria-labelledby="contacts-heading" className="flex flex-col gap-3">
        <h3 id="contacts-heading" className="text-lg font-medium">
          Emergency contacts
        </h3>
        <ResourcePanel resource={emergencyContacts(employeeId, self)} canManage={canEdit} />
      </section>
      {self ? null : (
        <>
          <section aria-labelledby="dependents-heading" className="flex flex-col gap-3">
            <h3 id="dependents-heading" className="text-lg font-medium">
              Dependents
            </h3>
            <ResourcePanel resource={dependents(employeeId)} canManage={canEdit} />
          </section>
          <section aria-labelledby="nominations-heading" className="flex flex-col gap-3">
            <h3 id="nominations-heading" className="text-lg font-medium">
              Nominations
            </h3>
            <Nominations employeeId={employeeId} canEdit={canEdit} />
          </section>
        </>
      )}
    </div>
  );
}
