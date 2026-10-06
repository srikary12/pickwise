// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import type { DashboardOut } from "@pickwise/api-client";
import { useQuery } from "@tanstack/react-query";

import { api, call } from "./api";

export const DASHBOARD_KEY = ["dashboard"] as const;

/**
 * The home page's data, which also feeds the sidebar and bell badges. One request, refreshed
 * every minute and whenever the window regains focus.
 */
export function useDashboard(enabled = true) {
  return useQuery({
    queryKey: DASHBOARD_KEY,
    queryFn: () => call<DashboardOut>(api().GET("/v1/dashboard")),
    enabled,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
    staleTime: 15_000,
  });
}

/** A link from the server is app-relative by contract; refuse anything else. */
export function safeLink(link: string | null | undefined): string | null {
  return link && link.startsWith("/") && !link.startsWith("//") ? link : null;
}
