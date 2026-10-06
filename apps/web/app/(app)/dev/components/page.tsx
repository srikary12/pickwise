// SPDX-License-Identifier: AGPL-3.0-only
// The components showcase is a development aid: a production build answers 404 here
// (make lighthouse checks that). It is a server component so the 404 is a real status code.
import { notFound } from "next/navigation";

import { Showcase } from "./showcase";

export default function ComponentsPage() {
  if (process.env.NODE_ENV === "production") notFound();
  return <Showcase />;
}
