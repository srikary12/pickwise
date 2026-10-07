// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { EmployeeOut, MemberOut } from "@pickwise/api-client";
import { Alert, Badge, Button, formatDate, Select, Tabs } from "@pickwise/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { NoAccess } from "@/components/no-access";
import { FamilyTab } from "@/components/people/family-tab";
import { IdentityTab } from "@/components/people/identity-tab";
import { JobTab } from "@/components/people/job-tab";
import { PersonalTab } from "@/components/people/personal-tab";
import { RecordsTab } from "@/components/people/records-tab";
import { api, call, errorMessage } from "@/lib/api";
import { showConflict } from "@/lib/conflict";
import { isStaleVersion } from "@/lib/query";
import { can, useSession } from "@/lib/session";

const STATUS_VARIANT = {
  active: "success",
  draft: "outline",
  pre_boarding: "warning",
  notice_period: "warning",
  leave_of_absence: "outline",
  exited: "destructive",
} as const;

function LinkUser({ employee, onDone }: { employee: EmployeeOut; onDone: () => void }) {
  const [choice, setChoice] = useState("");
  const [error, setError] = useState<string | null>(null);
  const members = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => call<MemberOut[]>(api().GET("/v1/admin/users")),
  });
  async function link(membershipId: string | null) {
    setError(null);
    try {
      await call(
        api().PUT("/v1/employees/{employee_id}/user", {
          params: { path: { employee_id: employee.id } },
          body: { membership_id: membershipId, row_version: employee.row_version },
        }),
      );
      onDone();
    } catch (caught) {
      if (isStaleVersion(caught)) showConflict();
      else setError(errorMessage(caught));
    }
  }
  if (employee.has_login) {
    return (
      <div className="flex items-center gap-2 text-sm">
        <Badge variant="outline">Can sign in</Badge>
        <Button size="sm" variant="outline" onClick={() => void link(null)}>
          Disconnect
        </Button>
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        aria-label="Connect to a person who can sign in"
        className="w-64"
        value={choice}
        onChange={(event) => setChoice(event.target.value)}
      >
        <option value="">Connect to a sign-in…</option>
        {(members.data ?? []).map((m) => (
          <option key={m.membership_id} value={m.membership_id}>
            {m.display_name} ({m.email})
          </option>
        ))}
      </Select>
      <Button size="sm" disabled={!choice} onClick={() => void link(choice)}>
        Connect
      </Button>
      {error ? <span className="text-sm text-destructive">{error}</span> : null}
    </div>
  );
}

function Profile() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const { data: session } = useSession();
  const [notice, setNotice] = useState<string | null>(null);
  const employee = useQuery({
    queryKey: ["employee", id],
    enabled: can(session, "core.employees.read"),
    queryFn: () =>
      call<EmployeeOut>(
        api().GET("/v1/employees/{employee_id}", { params: { path: { employee_id: id } } }),
      ),
  });

  if (!session) return null;
  if (!can(session, "core.employees.read")) return <NoAccess />;
  if (employee.isError) return <Alert variant="destructive">{errorMessage(employee.error)}</Alert>;
  const e = employee.data;
  if (!e) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading…
      </p>
    );
  }

  const canUpdate = can(session, "core.employees.update");
  const personal = can(session, "core.employee.personal.read");
  const bank = can(session, "core.employee.bank.read");
  const identity = can(session, "core.employee.identity.read");
  const tabs = [
    { id: "job", label: "Job" },
    ...(personal ? [{ id: "personal", label: "Personal" }] : []),
    ...(personal ? [{ id: "family", label: "Family" }] : []),
    ...(identity || bank ? [{ id: "identity", label: "Identity & bank" }] : []),
    { id: "records", label: "Education, experience & documents" },
  ];
  const requested = params.get("tab");
  const tab = tabs.find((t) => t.id === requested)?.id ?? "job";

  async function setStatus(to: "pre_boarding" | "active") {
    if (!e) return;
    setNotice(null);
    try {
      await call(
        api().POST("/v1/employees/{employee_id}/status", {
          params: { path: { employee_id: e.id } },
          body: { to, row_version: e.row_version },
        }),
      );
    } catch (caught) {
      if (isStaleVersion(caught)) showConflict();
      else setNotice(errorMessage(caught));
    }
    await queryClient.invalidateQueries({ queryKey: ["employee", id] });
  }

  return (
    <div className="flex flex-col gap-4" data-testid="employee-profile">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{e.display_name}</h1>
          <p className="text-sm text-muted-foreground">
            {e.employee_code} · joined {formatDate(e.date_of_joining)}
            {e.job ? ` · ${e.job.designation_name}, ${e.job.department_name}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={STATUS_VARIANT[e.status]} data-testid="employee-status">
            {e.status.replace("_", " ")}
          </Badge>
          {canUpdate && e.status === "draft" ? (
            <Button size="sm" variant="outline" onClick={() => void setStatus("pre_boarding")}>
              Start onboarding
            </Button>
          ) : null}
          {canUpdate && (e.status === "draft" || e.status === "pre_boarding") ? (
            <Button size="sm" onClick={() => void setStatus("active")}>
              Make active
            </Button>
          ) : null}
        </div>
      </header>
      {canUpdate ? (
        <LinkUser
          employee={e}
          onDone={() => void queryClient.invalidateQueries({ queryKey: ["employee", id] })}
        />
      ) : null}
      {notice ? <Alert variant="destructive">{notice}</Alert> : null}
      <Tabs
        label="Employee record"
        tabs={tabs}
        value={tab}
        onValueChange={(next) => router.replace(`/employees/${id}?tab=${next}`)}
      >
        {tab === "job" ? (
          <JobTab employee={e} canChange={can(session, "core.jobrecords.change")} />
        ) : null}
        {tab === "personal" ? (
          <PersonalTab employeeId={e.id} canEdit={can(session, "core.employee.personal.update")} />
        ) : null}
        {tab === "family" ? (
          <FamilyTab employeeId={e.id} canEdit={can(session, "core.employee.personal.update")} />
        ) : null}
        {tab === "identity" ? (
          <IdentityTab
            employeeId={e.id}
            canIdentity={identity}
            canEdit={can(session, "core.employee.identity.update")}
            canReveal={can(session, "core.employee.identity.reveal")}
            canBank={bank}
            canEditBank={can(session, "core.employee.bank.update")}
            canRevealBank={can(session, "core.employee.bank.reveal")}
          />
        ) : null}
        {tab === "records" ? <RecordsTab employeeId={e.id} canEdit={canUpdate} /> : null}
      </Tabs>
    </div>
  );
}

export default function EmployeeProfilePage() {
  return (
    <Suspense>
      <Profile />
    </Suspense>
  );
}
