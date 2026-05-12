"use client";

import { color, radius } from "@/lib/theme";

interface Props {
  value: string;
  onChange: (v: string) => void;
  remember: boolean;
  onRememberChange: (v: boolean) => void;
  pageHasBinding: boolean;
}

export function WorkingDirInput({
  value,
  onChange,
  remember,
  onRememberChange,
  pageHasBinding,
}: Props) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <label
        style={{
          fontSize: 12,
          color: color.text.secondary,
          fontWeight: 600,
        }}
      >
        Working directory
      </label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="(leave blank for scratch directory)"
        autoComplete="off"
        spellCheck={false}
        style={{
          padding: "8px 10px",
          border: `1px solid ${color.border.default}`,
          borderRadius: radius.sm,
          fontSize: 14,
          fontFamily: "ui-monospace, Menlo, monospace",
        }}
      />
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: 12,
          color: color.text.muted,
        }}
      >
        <input
          type="checkbox"
          checked={remember}
          onChange={(e) => onRememberChange(e.target.checked)}
        />
        {pageHasBinding
          ? "Update default for this page"
          : "Remember as default for this page"}
      </label>
    </div>
  );
}
