"use client";

import styles from "./ToolStatusBadge.module.css";

type Status = "ok" | "warn" | "muted";

const GLYPHS: Record<Status, string> = {
  ok: "✓",
  warn: "⚠",
  muted: "·",
};

export function ToolStatusBadge({
  status,
  label,
}: {
  status: Status;
  label: string;
}) {
  return (
    <span className={`${styles.badge} ${styles[status]}`}>
      <span aria-hidden="true">{GLYPHS[status]}</span>
      {label}
    </span>
  );
}
