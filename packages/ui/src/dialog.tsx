// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import { type ReactNode, useEffect, useRef } from "react";

import { cn } from "./lib/utils";

/** A modal built on the native <dialog> element: focus trap, Escape and backdrop come free. */
export function Dialog({
  open,
  onClose,
  title,
  children,
  className,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-label={title}
      onClose={onClose}
      className={cn(
        "m-auto w-full max-w-md rounded-xl border border-border bg-card p-6 text-card-foreground shadow-lg backdrop:bg-black/40",
        className,
      )}
    >
      <h2 className="mb-4 text-lg font-semibold">{title}</h2>
      {open ? children : null}
    </dialog>
  );
}
