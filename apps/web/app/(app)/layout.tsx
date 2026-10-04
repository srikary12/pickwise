// SPDX-License-Identifier: AGPL-3.0-only
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";

export default function AppLayout({ children }: { children: ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
