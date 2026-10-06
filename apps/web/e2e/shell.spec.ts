// SPDX-License-Identifier: AGPL-3.0-only
// The application shell: permission-driven navigation, theme, search, the notification bell,
// the tenant logo and the shared "this changed under you" prompt.
import { expect, type Page, test } from "@playwright/test";

import { ACME, adminPage, createMember, DEMO_PASSWORD } from "./support/flows";
import { reachObjectStore } from "./support/objectstore";

test.beforeAll(() => {
  if (!DEMO_PASSWORD) throw new Error("DEMO_PASSWORD is not set: run via `make e2e`");
});

// A 1x1 PNG.
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

async function csrf(page: Page): Promise<string> {
  const body = (await (await page.request.get("/api/v1/auth/csrf")).json()) as {
    csrf_token: string;
  };
  return body.csrf_token;
}

test("an admin sees every section; an employee only their own", async ({ page, browser }) => {
  const admin = await adminPage(browser, ACME);
  await admin.goto("/");
  const adminNav = admin.getByRole("navigation", { name: "Main" });
  for (const name of [
    "Home",
    "Approvals",
    "Organisation",
    "Users",
    "Roles",
    "Imports",
    "Webhooks",
    "Audit",
    "Branding",
  ]) {
    await expect(adminNav.getByRole("link", { name, exact: true })).toBeVisible();
  }
  await admin.context().close();

  await createMember(browser, page, ACME, "shell");
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Notifications" })).toBeVisible();
  for (const name of ["Users", "Roles", "Imports", "Webhooks", "Audit", "Branding"]) {
    await expect(nav.getByRole("link", { name, exact: true })).toHaveCount(0);
  }
  await page.goto("/admin/branding");
  await expect(page.getByTestId("no-access")).toBeVisible();

  // The palette offers only what this person may open or find.
  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("admin");
  await expect(page.getByRole("option", { name: /Audit/ })).toHaveCount(0);
  await expect(page.getByText("No matches.")).toBeVisible();
});

test("the theme can be chosen and is remembered", async ({ browser }) => {
  const page = await adminPage(browser, ACME);
  await page.goto("/");
  const html = page.locator("html");
  const toggle = page.getByTestId("theme-toggle");
  await expect(html).not.toHaveAttribute("data-theme", /.+/); // system by default
  await toggle.click();
  await expect(html).toHaveAttribute("data-theme", "light");
  await toggle.click();
  await expect(html).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(html).toHaveAttribute("data-theme", "dark"); // no flash back to light
  await toggle.click(); // back to system
  await expect(html).not.toHaveAttribute("data-theme", /.+/);
  await page.context().close();
});

test("search finds people and pages from the keyboard", async ({ browser }) => {
  const page = await adminPage(browser, ACME);
  await page.goto("/");
  await expect(page.getByTestId("welcome")).toBeVisible(); // the shortcut is live once the shell is
  await page.keyboard.press("Control+k");
  const input = page.getByTestId("palette-input");
  await expect(input).toBeFocused();

  await input.fill("admin@acme");
  const person = page.getByRole("option", { name: /admin@acme\.test/ });
  await expect(person).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/admin\/users$/);

  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("aud");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/admin\/audit$/);

  // Escape closes the palette.
  await page.keyboard.press("Control+k");
  await expect(page.getByTestId("palette-input")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("palette-input")).toBeHidden();
  await page.context().close();
});

test("the bell shows unread notifications and marks them read", async ({ browser }) => {
  const page = await adminPage(browser, ACME);
  // Notifications are created by modules (approvals, …) with no UI of their own yet, so the
  // API's answers are staged here; the server side is covered by the integration tests.
  const now = new Date().toISOString();
  const item = (id: string, title: string, read: boolean) => ({
    id,
    type: "system.notice",
    title,
    body: "Details",
    link: "/notifications",
    entity_type: null,
    entity_id: null,
    read_at: read ? now : null,
    created_at: now,
  });
  const items = [
    item("00000000-0000-4000-8000-000000000001", "Leave approved", false),
    item("00000000-0000-4000-8000-000000000002", "Payslip ready", false),
  ];
  let unread = items.length;
  const reads: string[] = [];
  await page.route("**/api/v1/dashboard", (route) =>
    route.fulfill({
      json: {
        pending_approvals: { count: 0, items: [] },
        notifications: { unread, items },
      },
    }),
  );
  await page.route("**/api/v1/notifications?**", (route) =>
    route.fulfill({ json: { items, next_cursor: null } }),
  );
  await page.route("**/api/v1/notifications/*/read", (route) => {
    reads.push(route.request().url());
    unread -= 1;
    return route.fulfill({ status: 204 });
  });

  await page.goto("/");
  await expect(page.getByTestId("bell-count")).toHaveText("2");
  await page.getByTestId("bell").click();
  await expect(page.getByRole("dialog", { name: "Notifications" })).toBeVisible();
  await page.getByRole("button", { name: /Leave approved/ }).click();
  await expect.poll(() => reads.length).toBe(1);
  expect(reads[0]).toContain("00000000-0000-4000-8000-000000000001");
  await expect(page).toHaveURL(/\/notifications$/);
  await expect(page.getByTestId("bell-count")).toHaveText("1");
  await page.context().close();
});

test("an admin sets and removes the tenant logo, and a stale save prompts a reload", async ({
  browser,
}) => {
  const page = await adminPage(browser, ACME);
  await reachObjectStore(page);
  await page.goto("/admin/branding");
  await expect(page.getByLabel("Choose a logo")).toBeEnabled();

  await page
    .getByLabel("Choose a logo")
    .setInputFiles({ name: "logo.png", mimeType: "image/png", buffer: PNG });
  await expect(page.getByText("Logo updated.")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("logo-preview")).toBeVisible();
  // The header carries it for everyone: the image URL is versioned, and the endpoint behind
  // it redirects to a signed object-store URL (the browser here can't reach the store itself).
  const src = await page.getByTestId("tenant-logo").getAttribute("src");
  expect(src).toMatch(/^\/api\/v1\/branding\/logo\?v=[0-9a-f-]{36}$/);
  const redirect = await page.request.get(src ?? "", { maxRedirects: 0 });
  expect(redirect.status()).toBe(302);
  expect(redirect.headers()["location"]).toContain("X-Amz-Signature");

  // Someone else changes the tenant after this page loaded it: the next save is stale.
  const settings = (await (await page.request.get("/api/v1/admin/tenant")).json()) as {
    mfa_required: boolean;
    row_version: number;
  };
  const bump = await page.request.patch("/api/v1/admin/tenant", {
    headers: { "X-CSRF-Token": await csrf(page) },
    data: { mfa_required: settings.mfa_required, row_version: settings.row_version },
  });
  expect(bump.status()).toBe(200);
  await page
    .getByLabel("Choose a logo")
    .setInputFiles({ name: "logo.png", mimeType: "image/png", buffer: PNG });
  await expect(
    page.getByRole("dialog", { name: "This changed while you were editing" }),
  ).toBeVisible({
    timeout: 30_000,
  });
  await page.getByTestId("conflict-reload").click();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  // After reloading, removing works.
  await page.getByRole("button", { name: "Remove logo" }).click();
  await expect(page.getByText("Logo removed.")).toBeVisible();
  await expect(page.getByTestId("tenant-logo")).toHaveCount(0);
  await page.context().close();
});
