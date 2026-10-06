// SPDX-License-Identifier: AGPL-3.0-only
// The one QueryClient, with the error handling every screen shares:
//   401 → the session is gone: clear it, and the shell sends the user to /login
//   409 stale_row_version → someone else changed the record: offer a reload (ConflictDialog)
// 403s are left to the page, which knows what to show instead (NoAccess).
import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import { ApiError } from "./api";
import { showConflict } from "./conflict";
import { SESSION_KEY } from "./session";

export function isStaleVersion(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409 && error.code === "stale_row_version";
}

export function createQueryClient(): QueryClient {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({ onError: (error) => handle(client, error) }),
    mutationCache: new MutationCache({ onError: (error) => handle(client, error) }),
    defaultOptions: { queries: { retry: false } },
  });
  return client;
}

function handle(client: QueryClient, error: unknown): void {
  if (error instanceof ApiError && error.status === 401) {
    client.setQueryData(SESSION_KEY, null);
  } else if (isStaleVersion(error)) {
    showConflict();
  }
}
