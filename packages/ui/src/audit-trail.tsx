// SPDX-License-Identifier: AGPL-3.0-only
import { formatDateTime } from "./format";

export interface AuditTrailEvent {
  id: string;
  occurred_at: string;
  actor_name: string | null;
  actor_type: string;
  action: string;
  /** Column → "[set]" / "[changed]" / "[removed]" (redacted), or [old, new]. */
  changes: Record<string, unknown> | null;
}

const REDACTED: Record<string, string> = {
  "[set]": "set",
  "[changed]": "changed",
  "[removed]": "removed",
};

function show(value: unknown): string {
  if (value === null || value === undefined) return "empty";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function Change({ column, value }: { column: string; value: unknown }) {
  if (typeof value === "string" && value in REDACTED) {
    return (
      <li>
        <span className="font-medium">{column}</span> {REDACTED[value]}{" "}
        <span className="text-muted-foreground">(value not recorded)</span>
      </li>
    );
  }
  if (Array.isArray(value) && value.length === 2) {
    return (
      <li>
        <span className="font-medium">{column}</span>: {show(value[0])} → {show(value[1])}
      </li>
    );
  }
  return (
    <li>
      <span className="font-medium">{column}</span>: {show(value)}
    </li>
  );
}

/** Who did what to a record, newest first. Values the audit trail redacts are never shown. */
export function AuditTrail({
  events,
  emptyMessage = "No changes recorded.",
}: {
  events: readonly AuditTrailEvent[];
  emptyMessage?: string;
}) {
  if (events.length === 0) return <p className="text-sm text-muted-foreground">{emptyMessage}</p>;
  return (
    <ol aria-label="Change history" className="flex flex-col divide-y divide-border">
      {events.map((event) => (
        <li key={event.id} className="py-3 text-sm" data-testid="audit-trail-event">
          <p>
            <span className="font-medium">{event.actor_name ?? event.actor_type}</span>{" "}
            <span className="font-mono text-xs">{event.action}</span>
          </p>
          <p className="text-xs text-muted-foreground">{formatDateTime(event.occurred_at)}</p>
          {event.changes && Object.keys(event.changes).length > 0 ? (
            <ul className="mt-1 list-disc pl-5 text-xs">
              {Object.entries(event.changes).map(([column, value]) => (
                <Change key={column} column={column} value={value} />
              ))}
            </ul>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
