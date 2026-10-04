// SPDX-License-Identifier: AGPL-3.0-only
// Page flows shared by the specs. Every address and password is generated per run.
import { type Browser, expect, type Page } from "@playwright/test";

import { linkFromEmail } from "./mailpit";
import { totp } from "./totp";

export const DEMO_PASSWORD = process.env.DEMO_PASSWORD ?? "";
export interface Org {
  slug: string;
  admin: string;
  name: string;
}
export const ACME: Org = { slug: "acme", admin: "admin@acme.test", name: "Acme Industries" };
export const GLOBEX: Org = {
  slug: "globex",
  admin: "admin@globex.test",
  name: "Globex Corporation",
};
export const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:3000";

/** The org's admin, already signed in (global-setup saved the session): sign-ins are rate limited. */
export async function adminPage(browser: Browser, org: Org): Promise<Page> {
  const context = await browser.newContext({
    baseURL: BASE_URL,
    storageState: `e2e/.auth/${org.slug}.json`,
  });
  return context.newPage();
}
export const NEW_PASSWORD = "e2e correct horse battery";

export function uniqueEmail(label: string): string {
  return `e2e-${label}-${Date.now()}-${Math.floor(Math.random() * 1e4)}@example.test`;
}

export async function signIn(page: Page, email: string, password: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

export async function signOut(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
}

/** As an admin: invite `email` into the current organisation with `role`. */
export async function invite(
  page: Page,
  email: string,
  { role = "Employee", name = "E2E Person" } = {},
): Promise<void> {
  await page.goto("/admin/users");
  await page.getByRole("button", { name: "Invite a person" }).click();
  const dialog = page.getByRole("dialog", { name: "Invite a person" });
  await dialog.getByLabel("Email").fill(email);
  await dialog.getByLabel("Name").fill(name);
  await dialog.getByLabel("Role").selectOption({ label: role });
  await dialog.getByRole("button", { name: "Send invitation" }).click();
  await expect(page.getByTestId(`member-${email}`)).toBeVisible();
}

export const INVITE_LINK = /https?:\/\/\S+\/invite\/[A-Za-z0-9_-]+/;
export const RESET_LINK = /https?:\/\/\S+\/reset-password\?token=[A-Za-z0-9_-]+/;

/** Accept the invitation emailed to `email` from `organisation`, choosing a password. */
export async function acceptInvite(
  page: Page,
  email: string,
  organisation: string,
  password = NEW_PASSWORD,
): Promise<void> {
  const link = await linkFromEmail(email, INVITE_LINK, { contains: organisation });
  await page.goto(link);
  await page.getByLabel("New password", { exact: true }).fill(password);
  await page.getByLabel("Confirm password").fill(password);
  await page.getByRole("button", { name: "Set password and join" }).click();
  await expect(page.getByTestId("welcome")).toBeVisible();
}

/** Enrol an authenticator on /settings/security. Returns the secret and the recovery codes. */
export async function enrolMfa(page: Page): Promise<{ secret: string; recoveryCodes: string[] }> {
  await page.goto("/settings/security");
  await page.getByRole("button", { name: "Set up two-factor authentication" }).click();
  await expect(page.getByRole("img", { name: /QR code/ })).toBeVisible();
  const secret = (await page.getByTestId("mfa-secret").textContent())?.trim() ?? "";
  expect(secret).toMatch(/^[A-Z2-7]{16,}$/);
  await page.getByLabel("Authentication code").fill(totp(secret));
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByTestId("recovery-codes")).toBeVisible();
  const recoveryCodes = await page.getByTestId("recovery-codes").locator("li").allTextContents();
  expect(recoveryCodes).toHaveLength(10);
  await page.getByRole("button", { name: "I've saved them" }).click();
  await expect(page.getByTestId("mfa-status")).toHaveText("On");
  return { secret, recoveryCodes };
}

/** An admin of `org` invites a fresh person, who then finishes joining in `page`. */
export async function createMember(
  browser: Browser,
  page: Page,
  org: Org,
  label: string,
  role = "Employee",
): Promise<string> {
  const email = uniqueEmail(label);
  const admin = await adminPage(browser, org);
  await invite(admin, email, { role });
  await admin.context().close();
  await acceptInvite(page, email, org.name);
  return email;
}
