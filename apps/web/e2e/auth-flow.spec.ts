// SPDX-License-Identifier: AGPL-3.0-only
// Phase 2's headline flow: invite → accept → login → MFA → switch tenant.
import { expect, test } from "@playwright/test";

import {
  ACME,
  acceptInvite,
  adminPage,
  DEMO_PASSWORD,
  enrolMfa,
  GLOBEX,
  invite,
  INVITE_LINK,
  NEW_PASSWORD,
  signIn,
  signOut,
  uniqueEmail,
} from "./support/flows";
import { linkFromEmail } from "./support/mailpit";
import { totp } from "./support/totp";

test.beforeAll(() => {
  if (!DEMO_PASSWORD) throw new Error("DEMO_PASSWORD is not set: run via `make e2e`");
});

test("invite → accept → login → MFA → switch tenant", async ({ page, browser }) => {
  const email = uniqueEmail("flow");

  await test.step("both organisations' admins invite the same person", async () => {
    for (const org of [ACME, GLOBEX]) {
      const admin = await adminPage(browser, org);
      await invite(admin, email);
      await expect(admin.getByTestId("tenant-name")).toHaveText(org.name);
      await admin.context().close();
    }
  });

  await test.step("they accept the first invitation and choose a password", async () => {
    await acceptInvite(page, email, ACME.name);
    await expect(page.getByTestId("tenant-name")).toHaveText(ACME.name);
    await expect(page.getByTestId("user-email")).toHaveText(email);
    // An employee sees no admin navigation.
    await expect(page.getByRole("link", { name: "Users" })).toHaveCount(0);
  });

  let secret = "";
  let recoveryCodes: string[] = [];
  await test.step("they turn on two-factor authentication", async () => {
    ({ secret, recoveryCodes } = await enrolMfa(page));
    await signOut(page);
  });

  await test.step("accepting the second invitation needs the second factor", async () => {
    const link = await linkFromEmail(email, INVITE_LINK, { contains: GLOBEX.name });
    await page.goto(link);
    await page.getByRole("button", { name: "Accept invitation" }).click();
    await expect(page).toHaveURL(/\/login\/mfa$/);
    // The code used to enrol is spent; the next time step is accepted (clock drift allowance).
    await page.getByLabel("Authentication code").fill(totp(secret, 1));
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByTestId("welcome")).toHaveText(`Welcome to ${GLOBEX.name}`);
  });

  await test.step("they switch organisation from the header", async () => {
    const switcher = page.getByLabel("Switch organisation");
    await switcher.selectOption({ label: ACME.name });
    await expect(page.getByTestId("welcome")).toHaveText(`Welcome to ${ACME.name}`);
    await switcher.selectOption({ label: GLOBEX.name });
    await expect(page.getByTestId("welcome")).toHaveText(`Welcome to ${GLOBEX.name}`);
    await signOut(page);
  });

  await test.step("a later login asks for the second factor; a recovery code works once", async () => {
    await signIn(page, email, NEW_PASSWORD);
    await expect(page).toHaveURL(/\/login\/mfa$/);
    await page.getByLabel("Authentication code").fill("000000");
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByText("That code isn't valid.")).toBeVisible();
    await page.getByRole("button", { name: "Use a recovery code instead" }).click();
    await page.getByLabel("Recovery code").fill(recoveryCodes[0] ?? "");
    await page.getByRole("button", { name: "Verify" }).click();
    // Two organisations and no active one: the user chooses.
    await expect(page).toHaveURL(/\/select-tenant$/);
    await page.getByRole("button", { name: ACME.name }).click();
    await expect(page.getByTestId("welcome")).toHaveText(`Welcome to ${ACME.name}`);
    await signOut(page);

    await signIn(page, email, NEW_PASSWORD);
    await page.getByRole("button", { name: "Use a recovery code instead" }).click();
    await page.getByLabel("Recovery code").fill(recoveryCodes[0] ?? "");
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByText("That recovery code isn't valid.")).toBeVisible();
  });
});

test("a wrong password gets the generic error", async ({ page }) => {
  await signIn(page, ACME.admin, "not the password at all");
  await expect(page.getByText("The email or password is incorrect")).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
});

test("an unknown invitation link is rejected", async ({ page }) => {
  await page.goto("/invite/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
  await expect(page.getByTestId("invite-invalid")).toBeVisible();
});

test("signed-out visitors are sent to the sign-in page", async ({ page }) => {
  await page.goto("/admin/users");
  await expect(page).toHaveURL(/\/login$/);
});
