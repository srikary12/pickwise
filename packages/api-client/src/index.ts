// SPDX-License-Identifier: AGPL-3.0-only
// Typed client for the Pickwise API. Request and response types are generated from
// the backend's OpenAPI schema (`make openapi`); never hand-write them.
import createClient, { type Client, type ClientOptions, type Middleware } from "openapi-fetch";

import type { components, paths } from "./schema";

export type { components, paths };
export type ApiClient = Client<paths>;
export type { Middleware };
export type Readiness = components["schemas"]["Readiness"];
export type SessionState = components["schemas"]["SessionState"];
export type MemberOut = components["schemas"]["MemberOut"];
export type RoleOut = components["schemas"]["RoleOut"];
export type PermissionOut = components["schemas"]["PermissionOut"];
export type AuditEventOut = components["schemas"]["AuditEventOut"];
export type EndpointOut = components["schemas"]["EndpointOut"];
export type DeliveryOut = components["schemas"]["DeliveryOut"];
export type ImportOut = components["schemas"]["ImportOut"];
export type ImportTypeOut = components["schemas"]["ImportTypeOut"];
export type DashboardOut = components["schemas"]["DashboardOut"];
export type NotificationOut = components["schemas"]["NotificationOut"];
export type InboxItem = components["schemas"]["InboxItem"];
export type RequestedItem = components["schemas"]["RequestedItem"];
export type SearchOut = components["schemas"]["SearchOut"];
export type BrandingOut = components["schemas"]["BrandingOut"];

export function createApiClient(
  baseUrl: string,
  options: Omit<ClientOptions, "baseUrl"> = {},
): ApiClient {
  return createClient<paths>({ ...options, baseUrl });
}
