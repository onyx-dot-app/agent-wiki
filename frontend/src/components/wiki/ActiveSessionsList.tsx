"use client";

import { Button } from "@/components/common/Button";
import { closeSession, useAgentSessions } from "@/lib/launchers";
import { color, radius } from "@/lib/theme";

export function ActiveSessionsList({ wikiPath }: { wikiPath: string }) {
  const { sessions, refresh } = useAgentSessions(wikiPath);
  const active = sessions.filter(
    (s) => s.status === "active" || s.status === "idle",
  );
  if (active.length === 0) return null;

  async function onClose(id: string) {
    if (!confirm("Close this agent session?")) return;
    await closeSession(id, "user_clicked");
    await refresh();
  }

  return (
    <div
      style={{
        padding: 8,
        background: color.accent.subtleBg,
        border: `1px solid ${color.accent.subtleBorder}`,
        borderRadius: radius.sm,
        fontSize: 12,
      }}
    >
      <div
        style={{
          fontWeight: 600,
          color: color.accent.subtleFg,
          marginBottom: 4,
        }}
      >
        {active.length} agent session{active.length > 1 ? "s" : ""} on this page
      </div>
      <ul
        style={{
          listStyle: "none",
          padding: 0,
          margin: 0,
          display: "flex",
          flexDirection: "column",
          gap: 4,
        }}
      >
        {active.map((s) => (
          <li
            key={s.id}
            style={{ display: "flex", alignItems: "center", gap: 8 }}
          >
            <span style={{ flex: 1, color: color.text.primary }}>
              {s.tool_id} · {s.status} · started {s.started_at}
            </span>
            <Button size="sm" variant="ghost" onClick={() => onClose(s.id)}>
              Close
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
