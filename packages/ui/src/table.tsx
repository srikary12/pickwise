// SPDX-License-Identifier: AGPL-3.0-only
import type { ComponentProps } from "react";

import { cn } from "./lib/utils";

export function Table({ className, ...props }: ComponentProps<"table">) {
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn("w-full text-left text-sm", className)} {...props} />
    </div>
  );
}

export function THead(props: ComponentProps<"thead">) {
  return <thead className="border-b border-border text-muted-foreground" {...props} />;
}

export function TBody(props: ComponentProps<"tbody">) {
  return <tbody className="divide-y divide-border" {...props} />;
}

export function Tr({ className, ...props }: ComponentProps<"tr">) {
  return <tr className={className} {...props} />;
}

export function Th({ className, ...props }: ComponentProps<"th">) {
  return <th scope="col" className={cn("px-3 py-2 font-medium", className)} {...props} />;
}

export function Td({ className, ...props }: ComponentProps<"td">) {
  return <td className={cn("px-3 py-2 align-top", className)} {...props} />;
}
