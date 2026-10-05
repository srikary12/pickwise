// SPDX-License-Identifier: AGPL-3.0-only
import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";
import { THEME_SCRIPT } from "@/lib/theme-script";

import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Pickwise",
  description: "Open-source HRMS for Indian companies",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en-IN" suppressHydrationWarning>
      <head>
        {/* Sets data-theme before first paint, so a saved dark choice never flashes light. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-screen antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
