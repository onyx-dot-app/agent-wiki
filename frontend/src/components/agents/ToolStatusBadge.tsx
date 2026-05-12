"use client";

import { color, radius } from "@/lib/theme";

type Status = "ok" | "warn" | "muted";

export function ToolStatusBadge({
  status,
  label,
}: {
  status: Status;
  label: string;
}) {
  const palette =
    status === "ok"
      ? {
          bg: color.state.success.bg,
          fg: color.state.success.fg,
          border: color.state.success.border,
          glyph: "✓",
        }
      : status === "warn"
        ? {
            bg: color.state.warning.bg,
            fg: color.state.warning.fg,
            border: color.state.warning.border,
            glyph: "⚠",
          }
        : {
            bg: color.bg.sunken,
            fg: color.text.muted,
            border: color.border.subtle,
            glyph: "·",
          };

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 8px",
        fontSize: 12,
        fontWeight: 500,
        color: palette.fg,
        background: palette.bg,
        border: `1px solid ${palette.border}`,
        borderRadius: radius.pill,
      }}
    >
      <span aria-hidden="true">{palette.glyph}</span>
      {label}
    </span>
  );
}
