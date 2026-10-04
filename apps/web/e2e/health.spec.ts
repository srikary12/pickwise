// SPDX-License-Identifier: AGPL-3.0-only
import { expect, test } from "@playwright/test";

test("health page shows the API as ready", async ({ page }) => {
  await page.goto("/status");
  await expect(page.getByRole("heading", { name: "API health" })).toBeVisible();
  await expect(page.getByTestId("api-status")).toContainText("ok");
  await expect(page.getByTestId("check-database")).toContainText("ok");
  await expect(page.getByTestId("check-object_storage")).toContainText("ok");
});
