// SPDX-License-Identifier: AGPL-3.0-only
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "./lib/utils";

const alertVariants = cva("rounded-md border px-3 py-2 text-sm", {
  variants: {
    variant: {
      info: "border-border bg-muted",
      destructive: "border-destructive/40 bg-destructive/10 text-destructive",
      success: "border-emerald-600/40 bg-emerald-600/10 text-emerald-700 dark:text-emerald-400",
      warning: "border-amber-500/50 bg-amber-500/10",
    },
  },
  defaultVariants: { variant: "info" },
});

export type AlertProps = ComponentProps<"div"> & VariantProps<typeof alertVariants>;

export function Alert({ className, variant, ...props }: AlertProps) {
  return (
    <div
      role={variant === "destructive" ? "alert" : "status"}
      className={cn(alertVariants({ variant }), className)}
      {...props}
    />
  );
}
