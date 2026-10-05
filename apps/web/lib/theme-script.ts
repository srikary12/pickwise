// SPDX-License-Identifier: AGPL-3.0-only
// Kept apart from lib/theme.ts (a client module) so the root layout, a server component, can
// read it as a plain string.
export const THEME_KEY = "pw_theme";

/** Runs in <head> before paint, so a saved choice never flashes the other theme. */
export const THEME_SCRIPT = `try{var t=localStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}`;
