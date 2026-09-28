// SPDX-License-Identifier: AGPL-3.0-only
// shadcn/ui badge, adapted for the shared package.
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "./lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        success: "border-transparent bg-emerald-600 text-white",
        destructive: "border-transparent bg-red-600 text-white",
        warning: "border-transparent bg-amber-500 text-black",
        outline: "text-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export type BadgeProps = ComponentProps<"span"> & VariantProps<typeof badgeVariants>;

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
