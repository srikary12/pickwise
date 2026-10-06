// SPDX-License-Identifier: AGPL-3.0-only
// The organisation screens: lists with add/edit/archive, registrations, and the department tree
// with its two ways to move a department.
import { expect, type Page, test } from "@playwright/test";

import { ACME, adminPage, createMember, DEMO_PASSWORD } from "./support/flows";

test.beforeAll(() => {
  if (!DEMO_PASSWORD) throw new Error("DEMO_PASSWORD is not set: run via `make e2e`");
});

const suffix = () => Math.random().toString(36).slice(2, 7).toUpperCase();

async function openTab(page: Page, tab: string) {
  await page.goto("/org");
  await expect(page.getByRole("heading", { name: "Organisation", level: 1 })).toBeVisible();
  await page.getByRole("tab", { name: tab }).click();
}

test("an admin manages entities, locations, designations and grades", async ({ browser }) => {
  const id = suffix();
  const page = await adminPage(browser, ACME);

  await openTab(page, "Legal entities");
  await page.getByRole("button", { name: "Add legal entity" }).click();
  const dialog = page.getByRole("dialog", { name: "Add legal entity" });
  await dialog.getByLabel("Name", { exact: true }).fill(`Org ${id}`);
  await dialog.getByLabel("Registered name").fill(`Org ${id} Private Limited`);
  await dialog.getByLabel("PAN").fill("not-a-pan");
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(dialog.getByText("PAN looks like AABCA1234F.").first()).toBeVisible();
  await dialog.getByLabel("PAN").fill("AABCA1234F");
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(page.getByTestId(`legal-entities-Org ${id}`)).toBeVisible();

  // A registration, then an overlapping one is refused with the server's reason.
  await page.getByRole("button", { name: `Registrations of Org ${id}` }).click();
  const registrations = page.getByRole("dialog", { name: `Registrations of Org ${id}` });
  const add = async (from: string, to: string) => {
    await registrations.getByLabel("State").selectOption("IN-KA");
    await registrations.getByLabel("Registration number").fill("PT-001");
    await registrations.getByLabel("Valid from").fill(from);
    await registrations.getByLabel("Valid until").fill(to);
    await registrations.getByRole("button", { name: "Add registration" }).click();
  };
  await add("2025-04-01", "2026-03-31");
  await expect(registrations.getByRole("cell", { name: "PT-001", exact: true })).toBeVisible();
  await add("2026-01-01", "");
  await expect(registrations.getByTestId("registration-error")).toContainText("overlaps");
  await registrations.getByRole("button", { name: "Close" }).click();

  await openTab(page, "Locations");
  await page.getByRole("button", { name: "Add location" }).click();
  const location = page.getByRole("dialog", { name: "Add location" });
  await location.getByLabel("Legal entity").selectOption({ label: `Org ${id}` });
  await location.getByLabel("Code", { exact: true }).fill(`LOC-${id}`);
  await location.getByLabel("Name", { exact: true }).fill(`Office ${id}`);
  await location.getByLabel("State").selectOption("IN-KA");
  await location.getByRole("button", { name: "Save" }).click();
  await expect(page.getByTestId(`locations-LOC-${id}`)).toContainText("Karnataka");

  await openTab(page, "Designations");
  await page.getByRole("button", { name: "Add designation" }).click();
  const designation = page.getByRole("dialog", { name: "Add designation" });
  await designation.getByLabel("Code", { exact: true }).fill(`D-${id}`);
  await designation.getByLabel("Name", { exact: true }).fill(`Engineer ${id}`);
  await designation.getByRole("button", { name: "Save" }).click();
  await expect(page.getByTestId(`designations-D-${id}`)).toBeVisible();
  // A second one with the same code is refused.
  await page.getByRole("button", { name: "Add designation" }).click();
  await designation.getByLabel("Code", { exact: true }).fill(`d-${id}`);
  await designation.getByLabel("Name", { exact: true }).fill("Duplicate");
  await designation.getByRole("button", { name: "Save" }).click();
  await expect(designation.getByTestId("org-form-error")).toContainText("already exists");
  await designation.getByRole("button", { name: "Cancel" }).click();

  // Archive hides it; "Show archived" brings it back, and it can be restored.
  await page.getByRole("button", { name: `Archive D-${id}` }).click();
  await expect(page.getByTestId(`designations-D-${id}`)).toHaveCount(0);
  await page.getByLabel("Show archived").check();
  await expect(page.getByTestId(`designations-D-${id}`)).toContainText("Archived");
  await page.getByRole("button", { name: `Restore D-${id}` }).click();
  await page.getByLabel("Show archived").uncheck();
  await expect(page.getByTestId(`designations-D-${id}`)).toBeVisible();

  await openTab(page, "Grades");
  await page.getByRole("button", { name: "Add grade" }).click();
  const grade = page.getByRole("dialog", { name: "Add grade" });
  await grade.getByLabel("Code", { exact: true }).fill(`G-${id}`);
  await grade.getByLabel("Name", { exact: true }).fill(`Grade ${id}`);
  await grade.getByLabel("Rank").fill("4");
  await grade.getByLabel("Annual CTC from (₹)").fill("2000000");
  await grade.getByLabel("Annual CTC up to (₹)").fill("1000000");
  await grade.getByRole("button", { name: "Save" }).click();
  await expect(grade.getByText("The minimum can't exceed the maximum.").first()).toBeVisible();
  await grade.getByLabel("Annual CTC up to (₹)").fill("3500000");
  await grade.getByRole("button", { name: "Save" }).click();
  await expect(page.getByTestId(`grades-G-${id}`)).toContainText("₹20,00,000.00");
  await page.context().close();
});

test("departments can be moved by dragging or from a menu, and never into themselves", async ({
  browser,
}) => {
  const id = suffix();
  const [a, b, c] = [`A${id}`, `B${id}`, `C${id}`];
  const page = await adminPage(browser, ACME);
  await openTab(page, "Departments");

  async function add(code: string, parent?: string) {
    if (parent)
      await page.getByRole("button", { name: `Add a department inside ${parent}` }).click();
    else await page.getByRole("button", { name: "Add department" }).click();
    const dialog = page.getByRole("dialog", { name: "Add department" });
    await dialog.getByLabel("Code", { exact: true }).fill(code);
    await dialog.getByLabel("Name").fill(code);
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(page.getByTestId(`department-${code}`)).toBeVisible();
  }
  const inside = (parent: string, child: string) =>
    page.locator(
      `li:has([data-testid="department-${parent}"]) li [data-testid="department-${child}"]`,
    );

  await add(a);
  await add(b);
  await add(c, a);
  await expect(inside(a, c)).toBeVisible();

  // From the menu: C goes under B. The list offers neither C itself nor anything below it.
  await page.getByRole("button", { name: `Move ${c}`, exact: true }).click();
  const move = page.getByRole("dialog", { name: `Move ${c}` });
  await expect(move.getByRole("option", { name: c })).toHaveCount(0);
  await move.getByLabel("New parent").selectOption({ label: b });
  await move.getByRole("button", { name: "Move" }).click();
  await expect(inside(b, c)).toBeVisible();

  // By dragging: B (with C inside) goes under A.
  await page.getByTestId(`department-${b}`).dragTo(page.getByTestId(`department-${a}`));
  await expect(inside(a, b)).toBeVisible();
  await expect(
    page.locator(
      `li:has([data-testid="department-${a}"]) li:has([data-testid="department-${b}"]) li [data-testid="department-${c}"]`,
    ),
  ).toBeVisible();

  // A can't be offered a place inside its own subtree.
  await page.getByRole("button", { name: `Move ${a}`, exact: true }).click();
  const moveA = page.getByRole("dialog", { name: `Move ${a}` });
  for (const code of [a, b, c]) {
    await expect(moveA.getByRole("option", { name: new RegExp(`${code}$`) })).toHaveCount(0);
  }
  await moveA.getByRole("button", { name: "Cancel" }).click();

  // A department with active sub-departments can't be archived.
  await page.getByRole("button", { name: `Archive ${a}`, exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "sub-departments" })).toBeVisible();
  await page.context().close();
});

test("an employee can look but not change", async ({ browser, page }) => {
  await createMember(browser, page, ACME, "org");
  await page.goto("/org");
  await expect(page.getByRole("heading", { name: "Organisation", level: 1 })).toBeVisible();
  await expect(page.getByRole("button", { name: "Add department" })).toHaveCount(0);
  await page.getByRole("tab", { name: "Legal entities" }).click();
  await expect(page.getByRole("button", { name: "Add legal entity" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Grades" })).toBeVisible();
});
