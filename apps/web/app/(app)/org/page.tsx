// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { Tabs } from "@pickwise/ui";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { DepartmentTree } from "@/components/org/department-tree";
import { ResourcePanel } from "@/components/org/resource-panel";
import {
  costCenters,
  designations,
  grades,
  legalEntities,
  locations,
} from "@/components/org/resources";
import { NoAccess } from "@/components/no-access";
import { can, useSession } from "@/lib/session";

const TABS = [
  { id: "departments", label: "Departments" },
  { id: "entities", label: "Legal entities" },
  { id: "locations", label: "Locations" },
  { id: "cost-centers", label: "Cost centres" },
  { id: "designations", label: "Designations" },
  { id: "grades", label: "Grades" },
] as const;

function Organisation() {
  const router = useRouter();
  const params = useSearchParams();
  const { data: session } = useSession();
  const requested = params.get("tab");
  const tab = TABS.find((t) => t.id === requested)?.id ?? "departments";
  const canManage = can(session, "core.org.manage");

  if (!session) return null;
  if (!can(session, "core.org.read")) return <NoAccess />;
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">Organisation</h1>
      <Tabs
        label="Organisation"
        tabs={TABS}
        value={tab}
        onValueChange={(id) => router.replace(`/org?tab=${id}`)}
      >
        {tab === "departments" ? <DepartmentTree canManage={canManage} /> : null}
        {tab === "entities" ? (
          <ResourcePanel resource={legalEntities} canManage={canManage} />
        ) : null}
        {tab === "locations" ? <ResourcePanel resource={locations} canManage={canManage} /> : null}
        {tab === "cost-centers" ? (
          <ResourcePanel resource={costCenters} canManage={canManage} />
        ) : null}
        {tab === "designations" ? (
          <ResourcePanel resource={designations} canManage={canManage} />
        ) : null}
        {tab === "grades" ? <ResourcePanel resource={grades} canManage={canManage} /> : null}
      </Tabs>
    </div>
  );
}

export default function OrganisationPage() {
  return (
    <Suspense>
      <Organisation />
    </Suspense>
  );
}
