// SPDX-License-Identifier: AGPL-3.0-only
import type { ReactNode } from "react";

import { Badge } from "./badge";
import { formatPeriod } from "./format";
import { cn } from "./lib/utils";
import { type Period, placeRecords } from "./timeline";

export interface TimelineItem extends Period {
  id: string;
  /** What this period says, e.g. "Senior Engineer · Hyderabad". */
  title: ReactNode;
  detail?: ReactNode;
}

/**
 * The history of an effective-dated fact (job record, compensation, bank account): newest first,
 * the one in force today highlighted, and any period with no record flagged. History is never
 * overwritten (CLAUDE.md rule 6), so every period stays visible. `today` is "YYYY-MM-DD".
 */
export function EffectiveDatedTimeline({
  items,
  today,
  label,
  emptyMessage = "No history yet.",
}: {
  items: readonly TimelineItem[];
  today: string;
  /** Names the list for screen readers, e.g. "Job history". */
  label: string;
  emptyMessage?: string;
}) {
  if (items.length === 0) {
    return <p className="text-sm text-muted-foreground">{emptyMessage}</p>;
  }
  return (
    <ol aria-label={label} className="flex flex-col gap-0">
      {placeRecords(items, today).map(({ record, state, gapBefore, overlapsOlder }) => (
        <li key={record.id} data-state={state} className="flex flex-col">
          <div
            className={cn(
              "rounded-md border px-3 py-2 text-sm",
              state === "current" ? "border-primary bg-muted" : "border-border bg-card",
            )}
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{record.title}</span>
              {state === "current" ? <Badge>Current</Badge> : null}
              {state === "future" ? <Badge variant="outline">Upcoming</Badge> : null}
            </div>
            <p className="text-xs text-muted-foreground">
              {formatPeriod(record.validFrom, record.validTo)}
            </p>
            {record.detail ? (
              <div className="mt-1 text-muted-foreground">{record.detail}</div>
            ) : null}
            {overlapsOlder ? (
              <p role="alert" className="mt-1 text-xs text-destructive">
                This period overlaps the one before it.
              </p>
            ) : null}
          </div>
          {gapBefore ? (
            <p className="px-3 py-1 text-xs text-warning" data-testid="timeline-gap">
              No record for the period between these two.
            </p>
          ) : (
            <span aria-hidden="true" className="ml-4 h-3 border-l border-border" />
          )}
        </li>
      ))}
    </ol>
  );
}
