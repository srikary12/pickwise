// SPDX-License-Identifier: AGPL-3.0-only
"use client";

import qrcode from "qrcode-generator";
import { useMemo } from "react";

const MARGIN = 2; // modules of quiet zone

/** Draws a QR code as an inline SVG path. Nothing leaves the browser, and no raw HTML is injected. */
export function QrCode({ value, label }: { value: string; label: string }) {
  const { size, path } = useMemo(() => {
    const qr = qrcode(0, "M");
    qr.addData(value);
    qr.make();
    const count = qr.getModuleCount();
    let d = "";
    for (let row = 0; row < count; row++) {
      for (let col = 0; col < count; col++) {
        if (qr.isDark(row, col)) d += `M${col + MARGIN} ${row + MARGIN}h1v1h-1z`;
      }
    }
    return { size: count + MARGIN * 2, path: d };
  }, [value]);

  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`0 0 ${size} ${size}`}
      shapeRendering="crispEdges"
      className="size-48 rounded-md border border-border bg-white"
    >
      <path d={path} fill="#000" />
    </svg>
  );
}
