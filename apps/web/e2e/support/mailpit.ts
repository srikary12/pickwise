// SPDX-License-Identifier: AGPL-3.0-only
// Reads the emails the app sent through Mailpit's HTTP API.
const MAILPIT = process.env.E2E_MAILPIT_URL ?? "http://localhost:8025";

interface Summary {
  ID: string;
}
interface Message {
  Text: string;
  Subject: string;
}

async function json<T>(path: string): Promise<T> {
  const response = await fetch(`${MAILPIT}${path}`);
  if (!response.ok) throw new Error(`Mailpit ${path}: ${response.status}`);
  return (await response.json()) as T;
}

/** Waits for an email to `to` whose text contains `contains`, and returns the first link matching `link`. */
export async function linkFromEmail(
  to: string,
  link: RegExp,
  { contains = "", timeoutMs = 30_000 }: { contains?: string; timeoutMs?: number } = {},
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const found = await json<{ messages: Summary[] }>(
      `/api/v1/search?query=${encodeURIComponent(`to:${to}`)}`,
    );
    for (const summary of found.messages) {
      const message = await json<Message>(`/api/v1/message/${summary.ID}`);
      if (!message.Text.includes(contains)) continue;
      const match = link.exec(message.Text);
      if (match) return match[0];
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`no email to ${to} containing ${link} within ${timeoutMs} ms`);
}
