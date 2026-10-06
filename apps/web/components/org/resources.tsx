// SPDX-License-Identifier: AGPL-3.0-only
"use client";

// What differs between the simple org lists: their fields, columns and API calls.
import type {
  CostCenterOut,
  DesignationOut,
  GradeOut,
  LegalEntityOut,
  LocationOut,
} from "@pickwise/api-client";
import { formatCurrency, SelectField, TextField } from "@pickwise/ui";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";

import { RegistrationsButton } from "@/components/org/registrations-dialog";
import { ArchivedBadge, type Resource } from "@/components/org/resource-panel";
import { api, call } from "@/lib/api";
import { INDIAN_STATES, stateName } from "@/lib/india";

const required = (label: string, max = 200) =>
  z.string().trim().min(1, `Enter ${label}.`).max(max, `Use at most ${max} characters.`);
const code = z
  .string()
  .trim()
  .regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,29}$/, "Use letters, digits, - or _ (up to 30).");
const optional = (pattern: RegExp, message: string) =>
  z
    .string()
    .trim()
    .refine((value) => value === "" || pattern.test(value), message);
const optionalText = (max: number) => z.string().trim().max(max, `Use at most ${max} characters.`);
const orNull = (value: string): string | null => (value === "" ? null : value);

/** Active legal entities, for "which entity" selects. */
function useEntityOptions() {
  const entities = useQuery({
    queryKey: ["org", "legal-entities", { archived: false }],
    queryFn: () =>
      call<LegalEntityOut[]>(api().GET("/v1/org/legal-entities", { params: { query: {} } })),
  });
  return (entities.data ?? []).map((entity) => ({ value: entity.id, label: entity.name }));
}

// --- legal entities ---------------------------------------------------------------------------

const entitySchema = z.object({
  name: required("a name"),
  legal_name: required("the registered name", 300),
  pan: optional(/^[A-Z]{5}[0-9]{4}[A-Z]$/, "PAN looks like AABCA1234F."),
  tan: optional(/^[A-Z]{4}[0-9]{5}[A-Z]$/, "TAN looks like BLRA12345B."),
  gstin: optional(
    /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/,
    "GSTIN has 15 characters, like 29AABCA1234F1Z5.",
  ),
  cin: optional(
    /^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$/,
    "CIN has 21 characters, like U12345KA2020PTC123456.",
  ),
  pf_establishment_code: optionalText(40),
  esi_employer_code: optionalText(40),
  address_line1: optionalText(200),
  address_city: optionalText(100),
  address_pincode: optional(/^[1-9][0-9]{5}$/, "A PIN code has 6 digits."),
});
type EntityValues = z.infer<typeof entitySchema>;

function entityBody(v: EntityValues) {
  return {
    name: v.name,
    legal_name: v.legal_name,
    country_code: "IN",
    pan: orNull(v.pan),
    tan: orNull(v.tan),
    gstin: orNull(v.gstin),
    cin: orNull(v.cin),
    pf_establishment_code: orNull(v.pf_establishment_code),
    esi_employer_code: orNull(v.esi_employer_code),
    registered_address: {
      ...(v.address_line1 ? { line1: v.address_line1 } : {}),
      ...(v.address_city ? { city: v.address_city } : {}),
      ...(v.address_pincode ? { pincode: v.address_pincode } : {}),
    },
  };
}

export const legalEntities: Resource<LegalEntityOut, EntityValues> = {
  id: "legal-entities",
  title: "Legal entities",
  singular: "legal entity",
  schema: entitySchema,
  defaults: {
    name: "",
    legal_name: "",
    pan: "",
    tan: "",
    gstin: "",
    cin: "",
    pf_establishment_code: "",
    esi_employer_code: "",
    address_line1: "",
    address_city: "",
    address_pincode: "",
  },
  toForm: (row) => {
    const address = row.registered_address as Record<string, string | undefined>;
    return {
      name: row.name,
      legal_name: row.legal_name,
      pan: row.pan ?? "",
      tan: row.tan ?? "",
      gstin: row.gstin ?? "",
      cin: row.cin ?? "",
      pf_establishment_code: row.pf_establishment_code ?? "",
      esi_employer_code: row.esi_employer_code ?? "",
      address_line1: address.line1 ?? "",
      address_city: address.city ?? "",
      address_pincode: address.pincode ?? "",
    };
  },
  Fields: ({ form }) => (
    <div className="grid gap-4 sm:grid-cols-2">
      <TextField form={form} name="name" label="Name" hint="How it appears in the app." />
      <TextField form={form} name="legal_name" label="Registered name" />
      <TextField form={form} name="pan" label="PAN" />
      <TextField form={form} name="tan" label="TAN" />
      <TextField form={form} name="gstin" label="GSTIN" />
      <TextField form={form} name="cin" label="CIN" />
      <TextField form={form} name="pf_establishment_code" label="PF establishment code" />
      <TextField form={form} name="esi_employer_code" label="ESI employer code" />
      <TextField form={form} name="address_line1" label="Address" className="sm:col-span-2" />
      <TextField form={form} name="address_city" label="City" />
      <TextField form={form} name="address_pincode" label="PIN code" />
    </div>
  ),
  columns: [
    {
      id: "name",
      header: "Name",
      hideable: false,
      cell: (row) => (
        <>
          <span className="font-medium">{row.name}</span>
          <ArchivedBadge row={row} />
          <div className="text-xs text-muted-foreground">{row.legal_name}</div>
        </>
      ),
    },
    { id: "pan", header: "PAN", cell: (row) => row.pan ?? "—" },
    { id: "gstin", header: "GSTIN", cell: (row) => row.gstin ?? "—", defaultHidden: true },
    { id: "tan", header: "TAN", cell: (row) => row.tan ?? "—", defaultHidden: true },
  ],
  list: (archived) =>
    call<LegalEntityOut[]>(
      api().GET("/v1/org/legal-entities", { params: { query: { include_archived: archived } } }),
    ),
  create: (v, key) =>
    call(
      api().POST("/v1/org/legal-entities", {
        body: entityBody(v),
        headers: { "Idempotency-Key": key },
      }),
    ),
  update: (row, v) =>
    call(
      api().PUT("/v1/org/legal-entities/{entity_id}", {
        params: { path: { entity_id: row.id } },
        body: { ...entityBody(v), row_version: row.row_version },
      }),
    ),
  setArchived: (row, archive) =>
    call(
      api().POST(
        archive
          ? "/v1/org/legal-entities/{entity_id}/archive"
          : "/v1/org/legal-entities/{entity_id}/unarchive",
        { params: { path: { entity_id: row.id } } },
      ),
    ),
  rowActions: (row, canManage) => <RegistrationsButton entity={row} canManage={canManage} />,
  rowLabel: (row) => row.name,
};

// --- locations --------------------------------------------------------------------------------

const decimal = (label: string) =>
  optional(/^-?\d{1,3}(\.\d{1,6})?$/, `${label} is a number like 12.9716.`);

const locationSchema = z.object({
  legal_entity_id: z.string().min(1, "Choose a legal entity."),
  code,
  name: required("a name"),
  state_code: z.string().min(1, "Choose a state."),
  city: optionalText(100),
  pincode: optional(/^[1-9][0-9]{5}$/, "A PIN code has 6 digits."),
  timezone: required("a timezone", 64),
  latitude: decimal("Latitude"),
  longitude: decimal("Longitude"),
  geofence_radius_m: optional(/^\d{2,6}$/, "Enter metres, 10 to 100000."),
});
type LocationValues = z.infer<typeof locationSchema>;

function locationBody(v: LocationValues) {
  return {
    legal_entity_id: v.legal_entity_id,
    code: v.code,
    name: v.name,
    state_code: v.state_code,
    city: orNull(v.city),
    pincode: orNull(v.pincode),
    timezone: v.timezone,
    latitude: orNull(v.latitude),
    longitude: orNull(v.longitude),
    geofence_radius_m: v.geofence_radius_m === "" ? null : Number(v.geofence_radius_m),
  };
}

export const locations: Resource<LocationOut, LocationValues> = {
  id: "locations",
  title: "Locations",
  singular: "location",
  schema: locationSchema,
  defaults: {
    legal_entity_id: "",
    code: "",
    name: "",
    state_code: "",
    city: "",
    pincode: "",
    timezone: "Asia/Kolkata",
    latitude: "",
    longitude: "",
    geofence_radius_m: "",
  },
  toForm: (row) => ({
    legal_entity_id: row.legal_entity_id,
    code: row.code,
    name: row.name,
    state_code: row.state_code,
    city: row.city ?? "",
    pincode: row.pincode ?? "",
    timezone: row.timezone ?? "Asia/Kolkata",
    latitude: row.latitude == null ? "" : String(row.latitude),
    longitude: row.longitude == null ? "" : String(row.longitude),
    geofence_radius_m: row.geofence_radius_m == null ? "" : String(row.geofence_radius_m),
  }),
  Fields: ({ form }) => {
    const entities = useEntityOptions();
    return (
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectField
          form={form}
          name="legal_entity_id"
          label="Legal entity"
          placeholder="Choose…"
          options={entities}
        />
        <TextField form={form} name="code" label="Code" hint="Short and unique, like BLR-HQ." />
        <TextField form={form} name="name" label="Name" />
        <SelectField
          form={form}
          name="state_code"
          label="State"
          placeholder="Choose…"
          options={INDIAN_STATES}
          hint="Decides professional tax and labour welfare fund rules."
        />
        <TextField form={form} name="city" label="City" />
        <TextField form={form} name="pincode" label="PIN code" />
        <TextField form={form} name="timezone" label="Timezone" />
        <TextField form={form} name="latitude" label="Latitude" />
        <TextField form={form} name="longitude" label="Longitude" />
        <TextField
          form={form}
          name="geofence_radius_m"
          label="Check-in radius (metres)"
          hint="Needs a latitude and longitude."
        />
      </div>
    );
  },
  columns: [
    {
      id: "name",
      header: "Location",
      hideable: false,
      cell: (row) => (
        <>
          <span className="font-medium">{row.name}</span>
          <ArchivedBadge row={row} />
          <div className="text-xs text-muted-foreground">{row.code}</div>
        </>
      ),
    },
    { id: "state", header: "State", cell: (row) => stateName(row.state_code) },
    { id: "city", header: "City", cell: (row) => row.city ?? "—" },
    {
      id: "geofence",
      header: "Check-in radius",
      cell: (row) => (row.geofence_radius_m ? `${row.geofence_radius_m} m` : "—"),
      defaultHidden: true,
    },
  ],
  list: (archived) =>
    call<LocationOut[]>(
      api().GET("/v1/org/locations", { params: { query: { include_archived: archived } } }),
    ),
  create: (v, key) =>
    call(
      api().POST("/v1/org/locations", {
        body: locationBody(v),
        headers: { "Idempotency-Key": key },
      }),
    ),
  update: (row, v) =>
    call(
      api().PUT("/v1/org/locations/{location_id}", {
        params: { path: { location_id: row.id } },
        body: { ...locationBody(v), row_version: row.row_version },
      }),
    ),
  setArchived: (row, archive) =>
    call(
      api().POST(
        archive
          ? "/v1/org/locations/{location_id}/archive"
          : "/v1/org/locations/{location_id}/unarchive",
        { params: { path: { location_id: row.id } } },
      ),
    ),
  rowLabel: (row) => row.code,
};

// --- cost centres -----------------------------------------------------------------------------

const costCenterSchema = z.object({
  legal_entity_id: z.string().min(1, "Choose a legal entity."),
  code,
  name: required("a name"),
});
type CostCenterValues = z.infer<typeof costCenterSchema>;

export const costCenters: Resource<CostCenterOut, CostCenterValues> = {
  id: "cost-centers",
  title: "Cost centres",
  singular: "cost centre",
  schema: costCenterSchema,
  defaults: { legal_entity_id: "", code: "", name: "" },
  toForm: (row) => ({ legal_entity_id: row.legal_entity_id, code: row.code, name: row.name }),
  Fields: ({ form }) => {
    const entities = useEntityOptions();
    return (
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectField
          form={form}
          name="legal_entity_id"
          label="Legal entity"
          placeholder="Choose…"
          options={entities}
          className="sm:col-span-2"
        />
        <TextField form={form} name="code" label="Code" />
        <TextField form={form} name="name" label="Name" />
      </div>
    );
  },
  columns: [
    {
      id: "name",
      header: "Cost centre",
      hideable: false,
      cell: (row) => (
        <>
          <span className="font-medium">{row.name}</span>
          <ArchivedBadge row={row} />
        </>
      ),
    },
    { id: "code", header: "Code", cell: (row) => row.code },
  ],
  list: (archived) =>
    call<CostCenterOut[]>(
      api().GET("/v1/org/cost-centers", { params: { query: { include_archived: archived } } }),
    ),
  create: (v, key) =>
    call(api().POST("/v1/org/cost-centers", { body: v, headers: { "Idempotency-Key": key } })),
  update: (row, v) =>
    call(
      api().PUT("/v1/org/cost-centers/{cost_center_id}", {
        params: { path: { cost_center_id: row.id } },
        body: { ...v, row_version: row.row_version },
      }),
    ),
  setArchived: (row, archive) =>
    call(
      api().POST(
        archive
          ? "/v1/org/cost-centers/{cost_center_id}/archive"
          : "/v1/org/cost-centers/{cost_center_id}/unarchive",
        { params: { path: { cost_center_id: row.id } } },
      ),
    ),
  rowLabel: (row) => row.code,
};

// --- designations -----------------------------------------------------------------------------

const designationSchema = z.object({
  code,
  name: required("a name"),
  job_family: optionalText(100),
});
type DesignationValues = z.infer<typeof designationSchema>;

export const designations: Resource<DesignationOut, DesignationValues> = {
  id: "designations",
  title: "Designations",
  singular: "designation",
  schema: designationSchema,
  defaults: { code: "", name: "", job_family: "" },
  toForm: (row) => ({ code: row.code, name: row.name, job_family: row.job_family ?? "" }),
  Fields: ({ form }) => (
    <div className="grid gap-4 sm:grid-cols-2">
      <TextField form={form} name="code" label="Code" />
      <TextField form={form} name="name" label="Name" />
      <TextField
        form={form}
        name="job_family"
        label="Job family"
        hint="Groups similar roles, like Engineering."
        className="sm:col-span-2"
      />
    </div>
  ),
  columns: [
    {
      id: "name",
      header: "Designation",
      hideable: false,
      cell: (row) => (
        <>
          <span className="font-medium">{row.name}</span>
          <ArchivedBadge row={row} />
        </>
      ),
    },
    { id: "code", header: "Code", cell: (row) => row.code },
    { id: "family", header: "Job family", cell: (row) => row.job_family ?? "—" },
  ],
  list: (archived) =>
    call<DesignationOut[]>(
      api().GET("/v1/org/designations", { params: { query: { include_archived: archived } } }),
    ),
  create: (v, key) =>
    call(
      api().POST("/v1/org/designations", {
        body: { ...v, job_family: orNull(v.job_family) },
        headers: { "Idempotency-Key": key },
      }),
    ),
  update: (row, v) =>
    call(
      api().PUT("/v1/org/designations/{designation_id}", {
        params: { path: { designation_id: row.id } },
        body: { ...v, job_family: orNull(v.job_family), row_version: row.row_version },
      }),
    ),
  setArchived: (row, archive) =>
    call(
      api().POST(
        archive
          ? "/v1/org/designations/{designation_id}/archive"
          : "/v1/org/designations/{designation_id}/unarchive",
        { params: { path: { designation_id: row.id } } },
      ),
    ),
  rowLabel: (row) => row.code,
};

// --- grades -----------------------------------------------------------------------------------

const amount = optional(/^\d{1,12}(\.\d{1,2})?$/, "Enter an amount like 1500000 or 1500000.50.");

const gradeSchema = z
  .object({
    code,
    name: required("a name"),
    rank: z
      .string()
      .trim()
      .regex(/^\d{1,5}$/, "Enter a whole number; higher is more senior."),
    ctc_min: amount,
    ctc_max: amount,
  })
  .refine((v) => v.ctc_min === "" || v.ctc_max === "" || Number(v.ctc_min) <= Number(v.ctc_max), {
    message: "The minimum can't exceed the maximum.",
    path: ["ctc_max"],
  });
type GradeValues = z.infer<typeof gradeSchema>;

function gradeBody(v: GradeValues) {
  return {
    code: v.code,
    name: v.name,
    rank: Number(v.rank),
    ctc_min: orNull(v.ctc_min),
    ctc_max: orNull(v.ctc_max),
  };
}

export const grades: Resource<GradeOut, GradeValues> = {
  id: "grades",
  title: "Grades",
  singular: "grade",
  schema: gradeSchema,
  defaults: { code: "", name: "", rank: "", ctc_min: "", ctc_max: "" },
  toForm: (row) => ({
    code: row.code,
    name: row.name,
    rank: String(row.rank),
    ctc_min: row.ctc_min == null ? "" : String(row.ctc_min),
    ctc_max: row.ctc_max == null ? "" : String(row.ctc_max),
  }),
  Fields: ({ form }) => (
    <div className="grid gap-4 sm:grid-cols-2">
      <TextField form={form} name="code" label="Code" />
      <TextField form={form} name="name" label="Name" />
      <TextField form={form} name="rank" label="Rank" hint="Higher is more senior." />
      <div />
      <TextField form={form} name="ctc_min" label="Annual CTC from (₹)" />
      <TextField form={form} name="ctc_max" label="Annual CTC up to (₹)" />
    </div>
  ),
  columns: [
    {
      id: "name",
      header: "Grade",
      hideable: false,
      cell: (row) => (
        <>
          <span className="font-medium">{row.name}</span>
          <ArchivedBadge row={row} />
          <div className="text-xs text-muted-foreground">{row.code}</div>
        </>
      ),
    },
    { id: "rank", header: "Rank", cell: (row) => row.rank },
    {
      id: "range",
      header: "Annual CTC range",
      cell: (row) =>
        row.ctc_min == null && row.ctc_max == null
          ? "—"
          : `${row.ctc_min == null ? "…" : formatCurrency(String(row.ctc_min))} – ${
              row.ctc_max == null ? "…" : formatCurrency(String(row.ctc_max))
            }`,
    },
  ],
  list: (archived) =>
    call<GradeOut[]>(
      api().GET("/v1/org/grades", { params: { query: { include_archived: archived } } }),
    ),
  create: (v, key) =>
    call(api().POST("/v1/org/grades", { body: gradeBody(v), headers: { "Idempotency-Key": key } })),
  update: (row, v) =>
    call(
      api().PUT("/v1/org/grades/{grade_id}", {
        params: { path: { grade_id: row.id } },
        body: { ...gradeBody(v), row_version: row.row_version },
      }),
    ),
  setArchived: (row, archive) =>
    call(
      api().POST(
        archive ? "/v1/org/grades/{grade_id}/archive" : "/v1/org/grades/{grade_id}/unarchive",
        { params: { path: { grade_id: row.id } } },
      ),
    ),
  rowLabel: (row) => row.code,
};
