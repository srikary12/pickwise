// SPDX-License-Identifier: AGPL-3.0-only
import type { Page } from "@playwright/test";

/** The object store is published on localhost:8333 for browsers, but this test's browser
 * shares the web container's network, where it is `s3:8333`. Forward those requests, and keep
 * what comes back so the test can read downloads without depending on browser download UI. */
export async function reachObjectStore(page: Page): Promise<{ url: string; body: string }[]> {
  const fetched: { url: string; body: string }[] = [];
  await page.route("http://localhost:8333/**", async (route) => {
    const url = route.request().url();
    // Presigned downloads sign the Host header, so keep the one the URL was signed for.
    const response = await route.fetch({
      url: url.replace("http://localhost:8333", "http://s3:8333"),
      headers: { ...route.request().headers(), host: "localhost:8333" },
    });
    if (route.request().method() === "GET") fetched.push({ url, body: await response.text() });
    await route.fulfill({ response });
  });
  return fetched;
}
