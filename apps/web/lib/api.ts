// SPDX-License-Identifier: AGPL-3.0-only
// The browser's API client. Same origin (ADR 0010): /api/* is proxied to the API, so
// session cookies are first-party and there is no CORS.
import { createApiClient, type Middleware } from "@pickwise/api-client";

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);
// Over https the cookie is __Host-prefixed (see the API's session module).
const CSRF_COOKIES = ["__Host-pw_csrf", "pw_csrf"];

function readCsrfCookie(): string | undefined {
  for (const part of document.cookie.split("; ")) {
    const [name, ...value] = part.split("=");
    if (name && CSRF_COOKIES.includes(name)) return decodeURIComponent(value.join("="));
  }
  return undefined;
}

/** Double-submit CSRF: echo the pw_csrf cookie in X-CSRF-Token on unsafe requests. */
const csrf: Middleware = {
  async onRequest({ request }) {
    if (!UNSAFE.has(request.method)) return undefined;
    let token = readCsrfCookie();
    if (!token) {
      await fetch("/api/v1/auth/csrf", { credentials: "same-origin" });
      token = readCsrfCookie();
    }
    if (token) request.headers.set("X-CSRF-Token", token);
    return request;
  },
};

let client: ReturnType<typeof createApiClient> | undefined;

/** Created lazily: it needs `document`, so it must only be used in the browser. */
export function api(): ReturnType<typeof createApiClient> {
  if (!client) {
    client = createApiClient("/api");
    client.use(csrf);
  }
  return client;
}

/** The API's error body is {"error": {"code", "message"}}. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryAfter?: number,
  ) {
    super(message);
  }
}

function describe(status: number, body: unknown, retryAfter: string | null): ApiError {
  const error = (body as { error?: { code?: string; message?: string } } | undefined)?.error;
  if (status === 429) {
    const seconds = Number(retryAfter) || undefined;
    return new ApiError(
      status,
      "rate_limited",
      seconds
        ? `Too many attempts. Try again in ${seconds} seconds.`
        : "Too many attempts. Try again later.",
      seconds,
    );
  }
  if (status === 422 && !error) {
    return new ApiError(status, "invalid", "Check the form and try again.");
  }
  return new ApiError(
    status,
    error?.code ?? "error",
    error?.message ?? "Something went wrong. Try again.",
  );
}

/** Unwrap an openapi-fetch result: the data on success, an ApiError otherwise. */
export async function call<T>(
  request: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let result;
  try {
    result = await request;
  } catch {
    throw new ApiError(0, "network", "Can't reach the server. Check your connection.");
  }
  if (!result.response.ok) {
    throw describe(
      result.response.status,
      result.error,
      result.response.headers.get("retry-after"),
    );
  }
  return result.data as T; // undefined for 204, which callers type as void
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong. Try again.";
}
