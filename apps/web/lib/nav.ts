// SPDX-License-Identifier: AGPL-3.0-only
// The sidebar. Modules add their own entries here as they get screens; an entry shows only
// when the signed-in user holds its permission (null = everyone).
export interface NavItem {
  href: string;
  label: string;
  permission: string | null;
  /** Which live count to show next to the label. */
  badge?: "approvals" | "notifications";
}

export interface NavGroup {
  label: string;
  items: readonly NavItem[];
}

export const NAV: readonly NavGroup[] = [
  {
    label: "Workspace",
    items: [
      { href: "/", label: "Home", permission: null },
      {
        href: "/approvals",
        label: "Approvals",
        permission: "platform.approvals.act",
        badge: "approvals",
      },
      { href: "/notifications", label: "Notifications", permission: null, badge: "notifications" },
      { href: "/org", label: "Organisation", permission: "core.org.read" },
    ],
  },
  {
    label: "Administration",
    items: [
      { href: "/admin/users", label: "Users", permission: "platform.users.read" },
      { href: "/admin/roles", label: "Roles", permission: "platform.roles.read" },
      { href: "/admin/imports", label: "Imports", permission: "platform.imports.run" },
      { href: "/admin/webhooks", label: "Webhooks", permission: "platform.webhooks.manage" },
      { href: "/admin/audit", label: "Audit", permission: "platform.audit.read" },
      { href: "/admin/branding", label: "Branding", permission: "platform.tenant.manage" },
    ],
  },
  {
    label: "Account",
    items: [{ href: "/settings/security", label: "Security", permission: null }],
  },
];

/** The flat list of every item (for the command palette). */
export const NAV_ITEMS: readonly NavItem[] = NAV.flatMap((group) => group.items);
