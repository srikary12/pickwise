// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";

import { ConflictDialog } from "@/components/conflict-dialog";
import { createQueryClient } from "@/lib/query";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(createQueryClient);
  return (
    <QueryClientProvider client={client}>
      {children}
      <ConflictDialog />
    </QueryClientProvider>
  );
}
