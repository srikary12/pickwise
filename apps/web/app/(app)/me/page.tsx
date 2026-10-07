// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { EmployeeOut } from "@pickwise/api-client";
import { Alert, Badge, formatDate, Tabs } from "@pickwise/ui";
import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { NoAccess } from "@/components/no-access";
import { FamilyTab } from "@/components/people/family-tab";
import { IdentityTab } from "@/components/people/identity-tab";
import { JobTab } from "@/components/people/job-tab";
import { PersonalTab } from "@/components/people/personal-tab";
import { api, ApiError, call, errorMessage } from "@/lib/api";
import { can, useSession } from "@/lib/session";

const TABS = [
  { id: "job", label: "Job" },
  { id: "personal", label: "Personal" },
  { id: "contacts", label: "Emergency contacts" },
  { id: "identity", label: "Identity & bank" },
] as const;

function MyProfile() {
  const router = useRouter();
  const params = useSearchParams();
  const { data: session } = useSession();
  const me = useQuery({
    queryKey: ["employee", "me"],
    enabled: can(session, "core.me.read"),
    retry: false,
    queryFn: () => call<EmployeeOut>(api().GET("/v1/me/employee")),
  });
  const tab = TABS.find((t) => t.id === params.get("tab"))?.id ?? "job";
  if (!session) return null;
  if (!can(session, "core.me.read")) return <NoAccess />;
  if (me.error instanceof ApiError && me.error.code === "no_employee_record") {
    return (
      <div className="flex flex-col gap-2" data-testid="no-employee-record">
        <h1 className="text-2xl font-semibold">My profile</h1>
        <Alert>
          You don&apos;t have an employee record yet. Ask HR to connect your sign-in to one.
        </Alert>
      </div>
    );
  }
  if (me.isError) return <Alert variant="destructive">{errorMessage(me.error)}</Alert>;
  const e = me.data;
  if (!e)
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading…
      </p>
    );
  const canEdit = can(session, "core.me.update");
  return (
    <div className="flex flex-col gap-4" data-testid="my-profile">
      <header>
        <h1 className="text-2xl font-semibold">My profile</h1>
        <p className="text-sm text-muted-foreground">
          {e.display_name} · {e.employee_code} · joined {formatDate(e.date_of_joining)}{" "}
          <Badge variant="outline">{e.status.replace("_", " ")}</Badge>
        </p>
      </header>
      <Tabs
        label="My profile"
        tabs={TABS}
        value={tab}
        onValueChange={(id) => router.replace(`/me?tab=${id}`)}
      >
        {tab === "job" ? <JobTab employee={e} canChange={false} self /> : null}
        {tab === "personal" ? <PersonalTab employeeId={e.id} canEdit={canEdit} self /> : null}
        {tab === "contacts" ? <FamilyTab employeeId={e.id} canEdit={canEdit} self /> : null}
        {tab === "identity" ? (
          <IdentityTab
            employeeId={e.id}
            self
            canIdentity
            canBank
            canEdit={false}
            canReveal={false}
            canEditBank={false}
            canRevealBank={false}
          />
        ) : null}
      </Tabs>
    </div>
  );
}

export default function MyProfilePage() {
  return (
    <Suspense>
      <MyProfile />
    </Suspense>
  );
}
