"use client";

import { color, radius } from "@/lib/theme";

import { ToolStatusBadge } from "./ToolStatusBadge";

interface Props {
  id: string;
  name: string;
  tagline: string;
  iconUrl: string;
  selected: boolean;
  onSelect?: () => void;
  tokenReady: boolean;
  helperReady: boolean;
  cliReady: boolean | null;
}

export function ToolCard({
  id,
  name,
  tagline,
  iconUrl,
  selected,
  onSelect,
  tokenReady,
  helperReady,
  cliReady,
}: Props) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
        textAlign: "left",
        width: "100%",
        padding: 12,
        background: selected ? color.accent.subtleBg : color.bg.page,
        border: `1px solid ${
          selected ? color.accent.bg : color.border.default
        }`,
        borderRadius: radius.md,
        cursor: onSelect ? "pointer" : "default",
        gap: 8,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          width: "100%",
        }}
      >
        <img src={iconUrl} alt="" width={24} height={24} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{ fontSize: 14, fontWeight: 600, color: color.text.primary }}
          >
            {name}
          </div>
          <div
            style={{
              fontSize: 12,
              color: color.text.muted,
              marginTop: 1,
            }}
          >
            {tagline}
          </div>
        </div>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <ToolStatusBadge
          status={tokenReady ? "ok" : "warn"}
          label={tokenReady ? "Token" : "Need token"}
        />
        <ToolStatusBadge
          status={helperReady ? "ok" : "warn"}
          label={helperReady ? "Launcher" : "No launcher"}
        />
        <ToolStatusBadge
          status={cliReady === null ? "muted" : cliReady ? "ok" : "warn"}
          label={
            cliReady === null ? "CLI: ?" : cliReady ? "CLI" : `${id} missing`
          }
        />
      </div>
    </button>
  );
}
