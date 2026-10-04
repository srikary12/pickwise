// SPDX-License-Identifier: AGPL-3.0-only
// Signs each demo organisation's admin in once and saves the session for the specs
// (sign-ins are rate limited per address, and the admins are used by every spec).
import { chromium } from "@playwright/test";

import { ACME, BASE_URL, DEMO_PASSWORD, GLOBEX, signIn } from "./support/flows";

export default async function globalSetup(): Promise<void> {
  if (!DEMO_PASSWORD) throw new Error("DEMO_PASSWORD is not set: run via `make e2e`");
  const browser = await chromium.launch();
  try {
    for (const org of [ACME, GLOBEX]) {
      const context = await browser.newContext({ baseURL: BASE_URL });
      const page = await context.newPage();
      await signIn(page, org.admin, DEMO_PASSWORD);
      await page.getByTestId("welcome").waitFor();
      await context.storageState({ path: `e2e/.auth/${org.slug}.json` });
      await context.close();
    }
  } finally {
    await browser.close();
  }
}
