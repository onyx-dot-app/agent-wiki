"use client";

import { Button, Card, Text } from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";

import { useConfirm } from "@/components/common/ConfirmDialog";
import { closeSession, useAgentSessions } from "@/lib/launchers";

export function ActiveSessionsList({ wikiPath }: { wikiPath: string }) {
  const { sessions, refresh } = useAgentSessions(wikiPath);
  const confirmDialog = useConfirm();
  const active = sessions.filter(
    (s) => s.status === "active" || s.status === "idle",
  );
  if (active.length === 0) return null;

  async function onClose(id: string) {
    if (
      !(await confirmDialog({
        title: "Close this agent session?",
        confirmLabel: "Close session",
      }))
    )
      return;
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
      <Section
        flexDirection="column"
        alignItems="start"
        justifyContent="start"
        gap={1}
        width="full"
      >
        <Text font="secondary-body" color="text-04" as="p">
          {`${active.length} external agent session${
            active.length > 1 ? "s" : ""
          } on this page`}
        </Text>
        {active.map((s) => (
          <Section
            key={s.id}
            flexDirection="row"
            alignItems="center"
            justifyContent="between"
            gap={0.5}
            width="full"
          >
            <Text font="secondary-body" color="text-03" nowrap>
              {`${s.tool_id} · ${s.status} · started ${s.started_at}`}
            </Text>
            <Button
              size="sm"
              prominence="tertiary"
              onClick={() => onClose(s.id)}
            >
              Close
            </Button>
          </Section>
        ))}
      </Section>
    </Card>
  );
}
