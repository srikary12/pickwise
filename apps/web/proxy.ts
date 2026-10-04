// SPDX-License-Identifier: AGPL-3.0-only
// Visits without a session cookie go straight to /login (saves a flash). The real
// check is the API's: pages also route by the session's stage (lib/session.ts).
import { type NextRequest, NextResponse } from "next/server";

const SESSION_COOKIES = ["__Host-pw_session", "pw_session"];

export function proxy(request: NextRequest): NextResponse {
  if (SESSION_COOKIES.some((name) => request.cookies.has(name))) return NextResponse.next();
  return NextResponse.redirect(new URL("/login", request.url));
}

export const config = {
  // Everything except the public pages, the API proxy and Next's own assets.
  matcher: [
    "/((?!login|forgot-password|reset-password|invite|status|healthz|api|_next|favicon.ico).*)",
  ],
};
