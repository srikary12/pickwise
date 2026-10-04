// SPDX-License-Identifier: AGPL-3.0-only
import { expect, test } from "@playwright/test";

import {
  ACME,
  adminPage,
  createMember,
  DEMO_PASSWORD,
  NEW_PASSWORD,
  RESET_LINK,
  signIn,
  signOut,
} from "./support/flows";
import { linkFromEmail } from "./support/mailpit";

test.beforeAll(() => {
  if (!DEMO_PASSWORD) throw new Error("DEMO_PASSWORD is not set: run via `make e2e`");
});

test("forgot password → email link → new password → sign in", async ({ page, browser }) => {
  const email = await createMember(browser, page, ACME, "reset");
  await signOut(page);

  await page.goto("/forgot-password");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Send reset link" }).click();
  await expect(page.getByTestId("reset-requested")).toBeVisible();

  const link = await linkFromEmail(email, RESET_LINK);
  await page.goto(link);
  const replacement = "a second correct horse battery";
  await page.getByLabel("New password", { exact: true }).fill("short");
  await page.getByLabel("Confirm password").fill("short");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByText("Use at least 12 characters.")).toBeVisible();
  await page.getByLabel("New password", { exact: true }).fill(replacement);
  await page.getByLabel("Confirm password").fill(replacement);
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByTestId("password-reset-done")).toBeVisible();

  // The old password is dead; the new one works.
  await signIn(page, email, NEW_PASSWORD);
  await expect(page.getByText("The email or password is incorrect")).toBeVisible();
  await signIn(page, email, replacement);
  await expect(page.getByTestId("welcome")).toBeVisible();
});

test("an employee can't open the admin pages; an admin can", async ({ page, browser }) => {
  const email = await createMember(browser, page, ACME, "access");
  await page.goto("/admin/users");
  await expect(page.getByTestId("no-access")).toBeVisible();
  await page.goto("/admin/roles");
  await expect(page.getByTestId("no-access")).toBeVisible();

  const admin = await adminPage(browser, ACME);
  await admin.goto("/");
  await admin.getByRole("link", { name: "Users" }).click();
  await expect(admin.getByTestId(`member-${email}`)).toBeVisible();
  await admin.getByRole("link", { name: "Roles" }).click();
  await expect(admin.getByTestId("role-tenant_admin")).toBeVisible();
  await admin.context().close();
});

test("an admin can suspend and reactivate a member", async ({ page, browser }) => {
  const email = await createMember(browser, page, ACME, "suspend");
  const admin = await adminPage(browser, ACME);
  await admin.goto("/admin/users");
  const row = admin.getByTestId(`member-${email}`);
  await row.getByRole("button", { name: "Suspend", exact: true }).click();
  await expect(row.getByText("suspended")).toBeVisible();
  await row.getByRole("button", { name: "Reactivate" }).click();
  await expect(row.getByText("active")).toBeVisible();
  await admin.context().close();
});

test("an admin can create a role with chosen permissions", async ({ browser }) => {
  const page = await adminPage(browser, ACME);
  await page.goto("/admin/roles");
  const key = `e2e_role_${Date.now()}`;
  await page.getByRole("button", { name: "New role" }).click();
  const dialog = page.getByRole("dialog", { name: "New role" });
  await dialog.getByLabel("Name", { exact: true }).fill("E2E viewer");
  await dialog.getByLabel("Key", { exact: true }).fill(key);
  await dialog.getByLabel("platform.users.read").check();
  await dialog.getByRole("button", { name: "Create role" }).click();
  await expect(page.getByTestId(`role-${key}`)).toContainText("1 permission");
  await page.context().close();
});
