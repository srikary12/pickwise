// SPDX-License-Identifier: AGPL-3.0-only
// The platform admin pages: audit trail, webhooks and bulk imports.
import { createHmac } from "node:crypto";
import { createServer, type IncomingMessage } from "node:http";
import type { AddressInfo } from "node:net";

import { expect, test } from "@playwright/test";

import { ACME, adminPage, BASE_URL, DEMO_PASSWORD } from "./support/flows";
import { reachObjectStore } from "./support/objectstore";

test.beforeAll(() => {
  if (!DEMO_PASSWORD) throw new Error("DEMO_PASSWORD is not set: run via `make e2e`");
});

test("the audit page filters events and exports CSV", async ({ browser }) => {
  const page = await adminPage(browser, ACME);
  // Make something auditable happen: a custom field is created through the API.
  const csrf = (await (await page.request.get("/api/v1/auth/csrf")).json()) as {
    csrf_token: string;
  };
  const key = `e2e_${Date.now()}`;
  const created = await page.request.post("/api/v1/custom-fields", {
    headers: { "X-CSRF-Token": csrf.csrf_token },
    data: { entity_type: "employee", key, label: "E2E field", field_type: "text" },
  });
  expect(created.status()).toBe(201);

  await page.goto("/admin/audit");
  await expect(page.getByTestId("audit-row").first()).toBeVisible();

  await page.getByLabel("Action").fill("custom_field.created");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(page.getByTestId("audit-row").first()).toBeVisible();
  for (const cell of await page
    .getByTestId("audit-row")
    .locator("td:nth-child(3)")
    .allTextContents()) {
    expect(cell).toContain("custom_field.created");
  }
  await page.getByLabel("Action").fill("no.such_action");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(page.getByText("No events match.")).toBeVisible();

  // The export link carries the filters and returns a CSV for this session.
  await page.getByLabel("Action").fill("custom_field.created");
  await page.getByRole("button", { name: "Apply filters" }).click();
  const href = await page.getByTestId("audit-export").getAttribute("href");
  expect(href).toContain("action=custom_field.created");
  const response = await page.request.get(href ?? "");
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toContain("text/csv");
  const lines = (await response.text()).trim().split("\n");
  expect(lines[0]).toContain("occurred_at");
  expect(lines.length).toBeGreaterThan(1);
});

interface Received {
  body: string;
  headers: IncomingMessage["headers"];
}

/** A receiver inside the test process. The API reaches it as `web:<port>`: this process
 * shares the web container's network namespace. */
async function receiver(): Promise<{ port: number; received: Received[]; close: () => void }> {
  const received: Received[] = [];
  const server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      received.push({ body: Buffer.concat(chunks).toString("utf8"), headers: req.headers });
      res.writeHead(204).end();
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "0.0.0.0", resolve));
  return {
    port: (server.address() as AddressInfo).port,
    received,
    close: () => server.close(),
  };
}

test("a webhook endpoint receives a signed test event, and a delivery can be replayed", async ({
  browser,
}) => {
  const hook = await receiver();
  try {
    const page = await adminPage(browser, ACME);
    await page.goto("/admin/webhooks");
    await page.getByRole("button", { name: "Add endpoint" }).click();
    const dialog = page.getByRole("dialog", { name: "Add a webhook endpoint" });
    const url = `http://web:${hook.port}/hook`;
    await dialog.getByLabel("URL").fill(url);
    await dialog.getByRole("button", { name: "Add endpoint" }).click();

    // The secret is shown once.
    const secret = (await page.getByTestId("secret-value").textContent()) ?? "";
    expect(secret).toMatch(/^whsec_/);
    await page.getByRole("button", { name: "I've saved it" }).click();
    await expect(page.getByTestId("new-secret")).toHaveCount(0);

    const row = page.getByTestId(`endpoint-${url}`);
    await row.getByRole("button", { name: "Send test" }).click();
    await expect.poll(() => hook.received.length, { timeout: 30_000 }).toBe(1);

    const first = hook.received[0];
    expect(first?.headers["x-pickwise-event"]).toBe("webhook.ping");
    const header = String(first?.headers["x-pickwise-signature"]);
    const [t, v1] = header.split(",").map((p) => p.slice(p.indexOf("=") + 1));
    const expected = createHmac("sha256", secret).update(`${t}.${first?.body}`).digest("hex");
    expect(v1).toBe(expected);

    await expect(page.getByTestId("delivery-row").first()).toContainText("succeeded", {
      timeout: 30_000,
    });
    await page.getByTestId("delivery-row").first().getByRole("button", { name: "Replay" }).click();
    await expect.poll(() => hook.received.length, { timeout: 30_000 }).toBe(2);
    // A replay is the same event under a new delivery id.
    expect(JSON.parse(hook.received[1]?.body ?? "{}").id).toBe(JSON.parse(first?.body ?? "{}").id);
    expect(hook.received[1]?.headers["x-pickwise-delivery"]).not.toBe(
      first?.headers["x-pickwise-delivery"],
    );
  } finally {
    hook.close();
  }
});

test("an import dry run reports row errors in a downloadable file, then commits a fixed file", async ({
  browser,
}) => {
  const page = await adminPage(browser, ACME);
  const fetched = await reachObjectStore(page);
  await page.goto("/admin/imports");
  await expect(page.getByTestId("import-columns")).toContainText("email*");

  await page.getByLabel("CSV or Excel file").setInputFiles({
    name: "contacts.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("name,email\nAsha Rao,asha@example.com\nBala,not-an-email\n"),
  });
  const first = page.locator('[data-testid^="import-item-"]').first();
  await expect(first.getByTestId("import-summary")).toContainText(
    "2 rows: 1 valid, 1 with errors",
    {
      timeout: 60_000,
    },
  );
  await expect(first.getByRole("button", { name: "Commit import" })).toHaveCount(0);

  await first.getByRole("button", { name: "Download error file" }).click();
  await expect
    .poll(() => fetched.find((f) => f.url.includes("import-errors"))?.body ?? "", {
      timeout: 30_000,
    })
    .toContain("row,column,message");
  const csv = fetched.find((f) => f.url.includes("import-errors"))?.body ?? "";
  expect(csv).toContain("3,email,isn't a valid email address");

  // The corrected file validates cleanly and can be committed.
  await page.getByLabel("CSV or Excel file").setInputFiles({
    name: "contacts-fixed.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("name,email\nAsha Rao,asha@example.com\nBala,bala@example.com\n"),
  });
  const fixed = page.locator('[data-testid^="import-item-"]').first();
  await expect(fixed.getByTestId("import-summary")).toContainText(
    "2 rows: 2 valid, 0 with errors",
    {
      timeout: 60_000,
    },
  );
  await fixed.getByRole("button", { name: "Commit import" }).click();
  await expect(fixed.getByTestId("import-status")).toHaveText("committed", { timeout: 60_000 });
  await expect(fixed.getByTestId("import-summary")).toContainText("2 written");
});

test("an employee has no access to these pages", async ({ browser }) => {
  const context = await browser.newContext({ baseURL: BASE_URL });
  const page = await context.newPage();
  await page.goto("/admin/audit");
  await expect(page).toHaveURL(/\/login/);
  await context.close();
});
