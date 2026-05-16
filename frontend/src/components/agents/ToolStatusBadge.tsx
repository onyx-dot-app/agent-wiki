"use client";

import { Tag } from "@onyx-ai/opal/components";

type Status = "ok" | "warn" | "muted";

const COLORS: Record<Status, "green" | "amber" | "gray"> = {
  ok: "green",
  warn: "amber",
  muted: "gray",
};

export function ToolStatusBadge({
  status,
  label,
}: {
  status: Status;
  label: string;
}) {
  return <Tag title={label} color={COLORS[status]} size="sm" />;
}
