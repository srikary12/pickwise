// SPDX-License-Identifier: AGPL-3.0-only
// Accessibility: axe over every screen in light and dark. Serious and critical violations fail.
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

import { ACME, adminPage, DEMO_PASSWORD } from "./support/flows";

test.beforeAll(() => {
  if (!DEMO_PASSWORD) throw new Error("DEMO_PASSWORD is not set: run via `make e2e`");
});

const PAGES = [
  { path: "/", ready: "welcome" },
  { path: "/notifications", heading: "Notifications" },
  { path: "/approvals", heading: "Approvals" },
  { path: "/admin/users", heading: "Users" },
  { path: "/admin/roles", heading: "Roles" },
  { path: "/admin/audit", heading: "Audit trail" },
  { path: "/admin/webhooks", heading: "Webhooks" },
  { path: "/admin/imports", heading: "Imports" },
  { path: "/admin/branding", heading: "Branding" },
  { path: "/settings/security", heading: "Security" },
  { path: "/dev/components", heading: "Components" },
] as const;

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

async function violations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  return results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => ({
      rule: v.id,
      impact: v.impact,
      help: v.help,
      targets: v.nodes.slice(0, 3).map((n) => n.target.join(" ")),
    }));
}

for (const theme of ["light", "dark"] as const) {
  test(`no serious accessibility violations (${theme})`, async ({ browser }) => {
    const page = await adminPage(browser, ACME);
    await page.addInitScript((value) => localStorage.setItem("pw_theme", value), theme);
    const found: Record<string, unknown> = {};
    for (const entry of PAGES) {
      await page.goto(entry.path);
      if ("ready" in entry) await expect(page.getByTestId(entry.ready)).toBeVisible();
      else await expect(page.getByRole("heading", { name: entry.heading, level: 1 })).toBeVisible();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await page.waitForLoadState("networkidle");
      const list = await violations(page);
      if (list.length > 0) found[entry.path] = list;
    }
    expect(found, JSON.stringify(found, null, 2)).toEqual({});
    await page.context().close();
  });
}

test("the command palette and the notification bell are accessible when open", async ({
  browser,
}) => {
  const page = await adminPage(browser, ACME);
  await page.goto("/");
  await expect(page.getByTestId("welcome")).toBeVisible();
  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("a");
  await expect(page.getByTestId("palette-input")).toBeFocused();
  expect(await violations(page)).toEqual([]);
  await page.keyboard.press("Escape");

  await page.getByTestId("bell").click();
  await expect(page.getByRole("dialog", { name: "Notifications" })).toBeVisible();
  expect(await violations(page)).toEqual([]);
  await page.context().close();
});

test("focus moves to the page after navigating, and the skip link works", async ({ browser }) => {
  const page = await adminPage(browser, ACME);
  await page.goto("/");
  await expect(page.getByTestId("welcome")).toBeVisible();
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Roles" }).click();
  await expect(page.getByRole("heading", { name: "Roles", level: 1 })).toBeVisible();
  await expect(page.locator("main")).toBeFocused();

  await page.reload();
  await expect(page.getByRole("heading", { name: "Roles", level: 1 })).toBeVisible(); // shell is up
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#main$/);
  await page.context().close();
});
