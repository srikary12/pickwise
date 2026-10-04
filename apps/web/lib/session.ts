// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { SessionState } from "@pickwise/api-client";
import { useQuery } from "@tanstack/react-query";

import { api, ApiError, call } from "./api";

export type Stage = SessionState["stage"];

export const SESSION_KEY = ["session"] as const;

/** Where a session at this stage belongs. The one place the stage machine is encoded. */
export function routeForStage(stage: Stage): string {
  switch (stage) {
    case "mfa_pending":
      return "/login/mfa";
    case "tenant_selection":
      return "/select-tenant";
    case "mfa_enrolment_required":
      return "/settings/security?required=1";
    case "ready":
      return "/";
  }
}

/** The current session, or null when signed out. */
export function useSession() {
  return useQuery({
    queryKey: SESSION_KEY,
    queryFn: async (): Promise<SessionState | null> => {
      try {
        return await call<SessionState>(api().GET("/v1/me"));
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    retry: false,
    staleTime: 30_000,
  });
}

export function can(session: SessionState | null | undefined, permission: string): boolean {
  return Boolean(session && permission in (session.permissions ?? {}));
}
