// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { useId, useRef, useState } from "react";

import { Alert } from "./alert";
import { Button } from "./button";
import { Input } from "./input";
import { Label } from "./label";

export type UploadStage = "uploading" | "scanning";

type State =
  | { kind: "idle" }
  | { kind: "working"; stage: UploadStage; name: string }
  | { kind: "done"; name: string }
  | { kind: "failed"; message: string };

/**
 * Pick a file, send it to the object store, then wait for the virus scan. Nothing counts as
 * uploaded until the scan says clean (files are quarantined until then). `upload` does the work
 * and reports its stage; it resolves with the file id, or rejects with a user-facing message
 * (an infected or rejected file arrives as a rejection). `signal` aborts when Cancel is pressed.
 */
export function FileUpload({
  label,
  accept,
  maxBytes,
  upload,
  onUploaded,
  disabled = false,
  hint,
}: {
  label: string;
  /** e.g. "image/png,image/jpeg" */
  accept?: string;
  maxBytes?: number;
  upload: (
    file: File,
    report: (stage: UploadStage) => void,
    signal: AbortSignal,
  ) => Promise<string>;
  onUploaded: (fileId: string, file: File) => void;
  disabled?: boolean;
  hint?: string;
}) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const abort = useRef<AbortController | null>(null);
  const [state, setState] = useState<State>({ kind: "idle" });

  async function onPick(file: File | undefined) {
    if (!file) return;
    if (maxBytes && file.size > maxBytes) {
      setState({
        kind: "failed",
        message: `That file is too large. The limit is ${Math.floor(maxBytes / 1024)} KB.`,
      });
      return;
    }
    const controller = new AbortController();
    abort.current = controller;
    setState({ kind: "working", stage: "uploading", name: file.name });
    try {
      const fileId = await upload(
        file,
        (stage) => setState({ kind: "working", stage, name: file.name }),
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setState({ kind: "done", name: file.name });
      onUploaded(fileId, file);
    } catch (error) {
      if (controller.signal.aborted) return;
      setState({
        kind: "failed",
        message: error instanceof Error ? error.message : "The upload didn't go through.",
      });
    } finally {
      if (input.current) input.current.value = "";
    }
  }

  const working = state.kind === "working";
  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        ref={input}
        type="file"
        accept={accept}
        disabled={disabled || working}
        aria-describedby={hint ? `${id}-hint` : undefined}
        onChange={(event) => void onPick(event.target.files?.[0])}
      />
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-muted-foreground">
          {hint}
        </p>
      ) : null}
      <div role="status" aria-live="polite" className="text-sm">
        {state.kind === "working" ? (
          <span className="flex items-center gap-3">
            {state.stage === "uploading"
              ? `Uploading ${state.name}…`
              : `Checking ${state.name} for viruses…`}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                abort.current?.abort();
                setState({ kind: "idle" });
              }}
            >
              Cancel
            </Button>
          </span>
        ) : null}
        {state.kind === "done" ? `${state.name} is ready.` : null}
      </div>
      {state.kind === "failed" ? <Alert variant="destructive">{state.message}</Alert> : null}
    </div>
  );
}
