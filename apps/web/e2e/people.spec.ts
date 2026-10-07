// SPDX-License-Identifier: AGPL-3.0-only
// People: adding an employee and walking them through onboarding, the profile tabs (job history,
// personal, family, identity and bank with masking and reveal), self-service, directory, org chart.
import { expect, type Page, test } from "@playwright/test";

import { violations } from "./support/axe";
import { ACME, adminPage, createMember, DEMO_PASSWORD } from "./support/flows";

test.beforeAll(() => {
  if (!DEMO_PASSWORD) throw new Error("DEMO_PASSWORD is not set: run via `make e2e`");
});

const tag = () => Math.random().toString(36).slice(2, 7).toUpperCase();

async function post(page: Page, path: string, data: object): Promise<{ id: string }> {
  const csrf = (await (await page.request.get("/api/v1/auth/csrf")).json()) as {
    csrf_token: string;
  };
  const response = await page.request.post(`/api${path}`, {
    data,
    headers: { "X-CSRF-Token": csrf.csrf_token },
  });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as { id: string };
}

/** A legal entity, location, department and two designations to hire into. */
async function seedOrg(page: Page, id: string) {
  const entity = await post(page, "/v1/org/legal-entities", {
    name: `People ${id}`,
    legal_name: `People ${id} Pvt Ltd`,
  });
  await post(page, "/v1/org/locations", {
    legal_entity_id: entity.id,
    code: `LOC-${id}`,
    name: `Office ${id}`,
    state_code: "IN-KA",
  });
  await post(page, "/v1/org/departments", { code: `DEP-${id}`, name: `Dept ${id}` });
  await post(page, "/v1/org/designations", { code: `DES-${id}`, name: `Engineer ${id}` });
  await post(page, "/v1/org/designations", { code: `SEN-${id}`, name: `Senior ${id}` });
  return {
    entity: `People ${id}`,
    location: `LOC-${id} · Office ${id}`,
    department: `Dept ${id}`,
    designation: `Engineer ${id}`,
    senior: `Senior ${id}`,
  };
}

function randomPan(): string {
  const letters = () =>
    Array.from({ length: 5 }, () => String.fromCharCode(65 + Math.floor(Math.random() * 26))).join(
      "",
    );
  return `${letters()}${String(Math.floor(Math.random() * 9000) + 1000)}${String.fromCharCode(65 + Math.floor(Math.random() * 26))}`;
}

test("HR adds an employee and fills in their record", async ({ browser }) => {
  const id = tag();
  const page = await adminPage(browser, ACME);
  const org = await seedOrg(page, id);

  await page.goto("/employees/new");
  await page.getByLabel("First name").fill(`Asha${id}`);
  await page.getByLabel("Last name").fill("Rao");
  await page.getByLabel("Joining date").fill("2025-01-06");
  await page.getByLabel("Legal entity").selectOption({ label: org.entity });
  await page.getByLabel("Location").selectOption({ label: org.location });
  await page.getByLabel("Department").selectOption({ label: org.department });
  await page.getByLabel("Designation").selectOption({ label: org.designation });
  await page.getByRole("button", { name: "Create employee" }).click();

  await expect(page.getByRole("heading", { name: `Asha${id} Rao`, level: 1 })).toBeVisible();
  await expect(page.getByTestId("employee-status")).toHaveText("draft");
  await page.getByRole("button", { name: "Start onboarding" }).click();
  await expect(page.getByTestId("employee-status")).toHaveText("pre boarding");
  await page.getByRole("button", { name: "Make active" }).click();
  await expect(page.getByTestId("employee-status")).toHaveText("active");

  // Job: a promotion from a past date splits the history into two records.
  await page.getByRole("button", { name: "Change job" }).click();
  const change = page.getByRole("dialog", { name: "Change job" });
  await change.getByLabel("Takes effect on").fill("2025-06-01");
  await change.getByLabel("Designation").selectOption({ label: org.senior });
  await change.getByRole("button", { name: "Save change" }).click();
  const history = page.getByRole("list", { name: "Job history" });
  await expect(history.getByRole("listitem")).toHaveCount(2);
  await expect(history.getByRole("listitem").first()).toContainText(org.senior);
  await expect(history.getByRole("listitem").first()).toContainText("Current");
  await expect(history.getByRole("listitem").last()).toContainText(org.designation);

  // Personal details and an address.
  await page.getByRole("tab", { name: "Personal" }).click();
  await page.getByLabel("Date of birth").fill("1992-03-14");
  await page.getByLabel("Nationality").fill("Indian");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Saved.")).toBeVisible();
  await page.getByTestId("address-current").getByRole("button", { name: "Add" }).click();
  const address = page.getByRole("dialog", { name: "Current address" });
  await address.getByLabel("Address line 1").fill("12 MG Road");
  await address.getByLabel("City").fill("Bengaluru");
  await address.getByRole("button", { name: "Save" }).click();
  await expect(page.getByTestId("address-current")).toContainText("12 MG Road");

  // Family: an emergency contact, a dependent, and nominations that must total 100.
  await page.getByRole("tab", { name: "Family" }).click();
  await page
    .getByTestId("org-contacts-" + (await employeeId(page)))
    .getByRole("button", { name: "Add contact" })
    .click();
  const contact = page.getByRole("dialog", { name: "Add contact" });
  await contact.getByLabel("Name").fill("Ravi");
  await contact.getByLabel("Relationship").fill("spouse");
  await contact.getByLabel("Phone").fill("9876543210");
  await contact.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("cell", { name: "Ravi", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Add dependent" }).click();
  const dependent = page.getByRole("dialog", { name: "Add dependent" });
  await dependent.getByLabel("Name").fill("Kid");
  await dependent.getByLabel("Relationship").selectOption("child");
  await dependent.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("cell", { name: "Kid", exact: true })).toBeVisible();
  const pf = page.getByTestId("nominations-pf");
  await pf.getByLabel("Provident fund share for Kid (percent)").fill("60");
  await expect(pf.getByRole("button", { name: "Save Provident fund" })).toBeDisabled();
  await pf.getByLabel("Provident fund share for Kid (percent)").fill("100");
  await pf.getByRole("button", { name: "Save Provident fund" }).click();
  await expect(pf).toContainText("Total 100%");

  // Identity and bank: masked everywhere, revealed on request.
  await page.getByRole("tab", { name: "Identity & bank" }).click();
  const pan = randomPan();
  await page.getByRole("button", { name: "Add document" }).click();
  const doc = page.getByRole("dialog", { name: "Add an identity document" });
  await doc.getByLabel("Document", { exact: true }).selectOption("pan");
  await doc.getByLabel("Number", { exact: true }).fill("not a pan");
  await doc.getByRole("button", { name: "Save" }).click();
  await expect(doc.getByTestId("table-empty").or(doc.getByRole("alert")).first()).toBeVisible();
  await doc.getByLabel("Number", { exact: true }).fill(pan);
  await doc.getByRole("button", { name: "Save" }).click();
  const row = page.getByTestId("identity-pan");
  await expect(row).toContainText(`••••${pan.slice(-4)}`);
  await expect(page.locator("body")).not.toContainText(pan);
  await row.getByRole("button", { name: /Reveal/ }).click();
  await expect(row.getByTestId("masked-value")).toHaveText(pan);

  await page.getByRole("button", { name: "Add account" }).click();
  const bank = page.getByRole("dialog", { name: "Add a bank account" });
  await bank.getByLabel("Account holder").fill(`Asha ${id}`);
  await bank.getByLabel("Bank", { exact: true }).fill("HDFC Bank");
  await bank.getByLabel("Account number", { exact: true }).fill("123456789012");
  await bank.getByLabel("IFSC").fill("HDFC0001234");
  await bank.getByRole("button", { name: "Save" }).click();
  await expect(page.getByTestId("bank-9012")).toContainText("••••9012");
  await expect(page.locator("body")).not.toContainText("123456789012");

  // Every tab is free of serious accessibility problems.
  const found: Record<string, unknown> = {};
  for (const tabName of ["job", "personal", "family", "identity", "records"]) {
    await page.goto(`/employees/${await employeeId(page)}?tab=${tabName}`);
    await page.waitForLoadState("networkidle");
    const list = await violations(page);
    if (list.length > 0) found[tabName] = list;
  }
  expect(found, JSON.stringify(found, null, 2)).toEqual({});
  await page.context().close();
});

async function employeeId(page: Page): Promise<string> {
  const match = /\/employees\/([0-9a-f-]{36})/.exec(page.url());
  if (!match?.[1]) throw new Error(`no employee id in ${page.url()}`);
  return match[1];
}

test("a person sees and edits their own record; colleagues find them in the directory", async ({
  browser,
  page,
}) => {
  const id = tag();
  const admin = await adminPage(browser, ACME);
  const org = await seedOrg(admin, id);
  const email = await createMember(browser, page, ACME, "people");

  await admin.goto("/employees/new");
  await admin.getByLabel("First name").fill(`Meera${id}`);
  await admin.getByLabel("Joining date").fill("2025-02-03");
  await admin.getByLabel("Legal entity").selectOption({ label: org.entity });
  await admin.getByLabel("Location").selectOption({ label: org.location });
  await admin.getByLabel("Department").selectOption({ label: org.department });
  await admin.getByLabel("Designation").selectOption({ label: org.designation });
  await admin.getByRole("button", { name: "Create employee" }).click();
  await expect(admin.getByTestId("employee-status")).toHaveText("draft");
  await admin.getByRole("button", { name: "Make active" }).click();
  await expect(admin.getByTestId("employee-status")).toHaveText("active");
  const connect = admin.getByLabel("Connect to a person who can sign in");
  await expect(admin.locator("option", { hasText: email })).toHaveCount(1);
  await connect.selectOption(
    (await admin.locator("option", { hasText: email }).getAttribute("value")) ?? "",
  );
  await admin.getByRole("button", { name: "Connect" }).click();
  await expect(admin.getByText("Can sign in")).toBeVisible();

  // The person: their own profile, with only contact details editable.
  await page.goto("/me");
  await expect(page.getByTestId("my-profile")).toContainText(`Meera${id}`);
  await page.getByRole("tab", { name: "Personal" }).click();
  await page.getByLabel("Personal email").fill("meera@home.test");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Saved.")).toBeVisible();
  await expect(
    page.getByText("Ask HR to change anything other than your email and phone."),
  ).toBeVisible();
  // …and has no way into the HR view.
  await page.goto("/employees");
  await expect(page.getByTestId("no-access")).toBeVisible();

  // Colleagues find them in the directory and search.
  await page.goto("/directory");
  await page.getByRole("textbox", { name: "Search" }).fill(`Meera${id}`);
  await expect(page.getByRole("cell", { name: new RegExp(`Meera${id}`) })).toBeVisible();
  await page.getByRole("tab", { name: "Org chart" }).click();
  await expect(page.getByTestId(`chart-Meera${id}`)).toBeVisible();
  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill(`Meera${id}`);
  await expect(page.getByRole("option", { name: new RegExp(`Meera${id}`) })).toBeVisible();

  // HR still sees them in the employee list; a member of staff without a record is told so.
  await admin.goto("/employees");
  await admin.getByRole("textbox", { name: "Search" }).fill(`Meera${id}`);
  await expect(admin.getByRole("link", { name: new RegExp(`Meera${id}`) })).toBeVisible();
  await admin.context().close();
});

test("someone without an employee record is told so", async ({ browser, page }) => {
  await createMember(browser, page, ACME, "norecord");
  await page.goto("/me");
  await expect(page.getByTestId("no-employee-record")).toBeVisible();
});
