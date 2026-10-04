// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { routeForStage, type Stage, useSession } from "@/lib/session";

/** For /login: a visitor who already has a session continues where it left off. */
export function useGuest() {
  const router = useRouter();
  const session = useSession();
  useEffect(() => {
    if (session.data) router.replace(routeForStage(session.data.stage));
  }, [session.data, router]);
  return session;
}

/** For pages that only make sense at one stage (e.g. the MFA step). */
export function useStageGuard(stage: Stage) {
  const router = useRouter();
  const session = useSession();
  useEffect(() => {
    if (session.isLoading) return;
    if (!session.data) router.replace("/login");
    else if (session.data.stage !== stage) router.replace(routeForStage(session.data.stage));
  }, [session.isLoading, session.data, stage, router]);
  return session;
}
