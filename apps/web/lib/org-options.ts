// SPDX-License-Identifier: AGPL-3.0-only
"use client";

// The choices behind the job selects (location, department, designation, grade, cost centre,
// manager). Active rows only, loaded once and shared through the query cache.
import type {
  CostCenterOut,
  DepartmentOut,
  DesignationOut,
  DirectoryPage,
  GradeOut,
  LocationOut,
} from "@pickwise/api-client";
import { useQuery } from "@tanstack/react-query";

import { api, call } from "./api";

export interface Option {
  value: string;
  label: string;
}

export interface OrgOptions {
  locations: Option[];
  departments: Option[];
  designations: Option[];
  grades: Option[];
  costCenters: Option[];
  managers: Option[];
  entities: Option[];
}

const EMPTY: OrgOptions = {
  locations: [],
  departments: [],
  designations: [],
  grades: [],
  costCenters: [],
  managers: [],
  entities: [],
};

export function useOrgOptions(): OrgOptions {
  const { data } = useQuery({
    queryKey: ["org", "options"],
    staleTime: 60_000,
    queryFn: async (): Promise<OrgOptions> => {
      const q = { params: { query: {} } };
      const [entities, locations, departments, designations, grades, costCenters, people] =
        await Promise.all([
          call<{ id: string; name: string }[]>(api().GET("/v1/org/legal-entities", q)),
          call<LocationOut[]>(api().GET("/v1/org/locations", q)),
          call<DepartmentOut[]>(api().GET("/v1/org/departments", q)),
          call<DesignationOut[]>(api().GET("/v1/org/designations", q)),
          call<GradeOut[]>(api().GET("/v1/org/grades", q)),
          call<CostCenterOut[]>(api().GET("/v1/org/cost-centers", q)),
          call<DirectoryPage>(api().GET("/v1/directory", { params: { query: { limit: 200 } } })),
        ]);
      return {
        entities: entities.map((e) => ({ value: e.id, label: e.name })),
        locations: locations.map((l) => ({ value: l.id, label: `${l.code} · ${l.name}` })),
        departments: departments.map((d) => ({
          value: d.id,
          label: `${"— ".repeat(d.depth)}${d.name}`,
        })),
        designations: designations.map((d) => ({ value: d.id, label: d.name })),
        grades: grades.map((g) => ({ value: g.id, label: `${g.code} · ${g.name}` })),
        costCenters: costCenters.map((c) => ({ value: c.id, label: `${c.code} · ${c.name}` })),
        managers: people.items.map((p) => ({
          value: p.id,
          label: `${p.display_name} (${p.employee_code})`,
        })),
      };
    },
  });
  return data ?? EMPTY;
}

export const EMPLOYMENT_TYPES: readonly Option[] = [
  { value: "full_time", label: "Full time" },
  { value: "part_time", label: "Part time" },
  { value: "fixed_term", label: "Fixed term" },
  { value: "contract", label: "Contract" },
  { value: "intern", label: "Intern" },
  { value: "apprentice", label: "Apprentice" },
  { value: "consultant", label: "Consultant" },
];
