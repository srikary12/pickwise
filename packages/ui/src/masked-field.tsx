// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useEffect, useState } from "react";

import { Button } from "./button";

const SHOW_FOR_MS = 30_000;

/**
 * A sensitive value shown masked ("XXXXXX1234"). "Reveal" asks the server, which checks the
 * caller's permission and writes a `pii.reveal` audit event, then shows the value for 30 seconds
 * and masks it again. The revealed value lives only in this component's state: never in a cache.
 */
export function MaskedField({
  label,
  masked,
  reveal,
  showForMs = SHOW_FOR_MS,
}: {
  label: string;
  masked: string;
  /** Resolves with the real value; rejects (with a user-facing message) when not allowed. */
  reveal?: () => Promise<string>;
  showForMs?: number;
}) {
  const [value, setValue] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (value === null) return;
    const timer = setTimeout(() => setValue(null), showForMs);
    return () => clearTimeout(timer);
  }, [value, showForMs]);

  async function onReveal() {
    if (!reveal) return;
    setBusy(true);
    setError(null);
    try {
      setValue(await reveal());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Couldn't reveal this value.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-1 text-sm">
      <span className="font-medium">{label}</span>
      <div className="flex items-center gap-2">
        <span className="font-mono tabular-nums" data-testid="masked-value" aria-live="polite">
          {value ?? masked}
        </span>
        {reveal ? (
          value === null ? (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              aria-label={`Reveal ${label}`}
              onClick={() => void onReveal()}
            >
              Reveal
            </Button>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              aria-label={`Hide ${label}`}
              onClick={() => setValue(null)}
            >
              Hide
            </Button>
          )
        ) : null}
      </div>
      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
