"use client";

import { Button, Card, Text } from "@onyx-ai/opal/components";

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
    try {
      await closeSession(id, "user_clicked");
    } catch (err) {
      // Surface the failure rather than silently leaving the row up —
      // user thought they closed it but the backend still has it open.
      alert(err instanceof Error ? err.message : "Failed to close session");
    } finally {
      // Refresh either way: a backend-side close that's no longer
      // visible to us (race) should still drop the row; a real failure
      // re-renders with whatever the backend currently reports.
      await refresh();
    }
  }

  return (
    <Card padding="sm" border="solid" rounding="sm">
      <Text font="secondary-body" color="text-04" as="p">
        {`${active.length} external agent session${
          active.length > 1 ? "s" : ""
        } on this page`}
      </Text>
      <ul className={styles.list}>
        {active.map((s) => (
          <li key={s.id} className={styles.row}>
            <Text font="secondary-body" color="text-03" nowrap>
              {`${s.tool_id} · ${s.status} · started ${s.started_at}`}
            </Text>
            <Button
              size="md"
              prominence="tertiary"
              onClick={() => onClose(s.id)}
            >
              Close
            </Button>
          </li>
        ))}
      </ul>
    </Card>
  );
}
