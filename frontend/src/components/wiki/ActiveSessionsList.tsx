"use client";

import { Button } from "@onyx-ai/opal/components";

import { closeSession, useAgentSessions } from "@/lib/launchers";

import styles from "./ActiveSessionsList.module.css";

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
    <div className={styles.wrapper}>
      <div className={styles.heading}>
        {active.length} agent session{active.length > 1 ? "s" : ""} on this page
      </div>
      <ul className={styles.list}>
        {active.map((s) => (
          <li key={s.id} className={styles.row}>
            <span className={styles.detail}>
              {s.tool_id} · {s.status} · started {s.started_at}
            </span>
            <Button
              size="sm"
              prominence="tertiary"
              onClick={() => onClose(s.id)}
            >
              Close
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
