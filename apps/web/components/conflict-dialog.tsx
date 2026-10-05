// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { Button, Dialog } from "@pickwise/ui";
import { useQueryClient } from "@tanstack/react-query";

import { dismissConflict, useConflictOpen } from "@/lib/conflict";

/** The shared "someone else changed this" prompt (HTTP 409 stale_row_version). */
export function ConflictDialog() {
  const open = useConflictOpen();
  const queryClient = useQueryClient();
  return (
    <Dialog open={open} onClose={dismissConflict} title="This changed while you were editing">
      <p className="text-sm text-muted-foreground">
        Someone else saved a change to this record after you opened it. Reload to see the latest
        version, then make your change again.
      </p>
      <div className="mt-6 flex justify-end gap-2">
        <Button variant="outline" onClick={dismissConflict}>
          Keep editing
        </Button>
        <Button
          data-testid="conflict-reload"
          onClick={() => {
            void queryClient.invalidateQueries();
            dismissConflict();
          }}
        >
          Reload
        </Button>
      </div>
    </Dialog>
  );
}
