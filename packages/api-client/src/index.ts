// SPDX-License-Identifier: AGPL-3.0-only
// Typed client for the Pickwise API. Request and response types are generated from
// the backend's OpenAPI schema (`make openapi`); never hand-write them.
import createClient, { type Client, type ClientOptions } from "openapi-fetch";

import type { components, paths } from "./schema";

export type { components, paths };
export type ApiClient = Client<paths>;
export type Readiness = components["schemas"]["Readiness"];

export function createApiClient(
  baseUrl: string,
  options: Omit<ClientOptions, "baseUrl"> = {},
): ApiClient {
  return createClient<paths>({ ...options, baseUrl });
}
