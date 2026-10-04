// SPDX-License-Identifier: AGPL-3.0-only
import { Alert } from "@pickwise/ui";

export function NoAccess() {
  return (
    <Alert data-testid="no-access">
      You don&apos;t have access to this page. Ask an administrator if you need it.
    </Alert>
  );
}
