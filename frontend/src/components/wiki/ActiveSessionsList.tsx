"use client";

import { Button, Card, Text } from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";
import { SvgExternalLink } from "@onyx-ai/opal/icons";

import { useConfirm } from "@/components/common/ConfirmDialog";
import { craftFailureMessage } from "@/components/wiki/CraftNotifier";
import {
  closeSession,
  useAgentSessions,
  type AgentSessionSummary,
} from "@/lib/launchers";

function isCraft(s: AgentSessionSummary): boolean {
  return s.tool_id === "onyx-craft";
}

function sessionLabel(s: AgentSessionSummary): string {
  if (isCraft(s)) {
    if (s.status === "provisioning") return "Onyx Craft · starting…";
    if (s.status === "ready") return "Onyx Craft · ready";
    if (s.status === "failed") return "Onyx Craft · failed";
  }
  return `${s.tool_id} · ${s.status} · started ${s.started_at}`;
}

export function ActiveSessionsList({ wikiPath }: { wikiPath: string }) {
  const { sessions, refresh } = useAgentSessions(wikiPath);
  const confirmDialog = useConfirm();
  const visible = sessions.filter((s) => {
    if (s.status === "active" || s.status === "idle") return true;
    // Craft lifecycle states worth surfacing on the page.
    if (isCraft(s)) {
      return (
        s.status === "provisioning" ||
        s.status === "ready" ||
        s.status === "failed"
      );
    }
    return false;
  });
  if (visible.length === 0) return null;

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

  function openCraft(url: string) {
    window.open(url, "_blank", "noopener,noreferrer");
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
          {`${visible.length} agent session${
            visible.length > 1 ? "s" : ""
          } on this page`}
        </Text>
        {visible.map((s) => (
          <Section
            key={s.id}
            flexDirection="column"
            alignItems="start"
            justifyContent="start"
            gap={0.25}
            width="full"
          >
            <Section
              flexDirection="row"
              alignItems="center"
              justifyContent="between"
              gap={0.5}
              width="full"
            >
              <Text font="secondary-body" color="text-03" nowrap>
                {sessionLabel(s)}
              </Text>
              <Section
                flexDirection="row"
                alignItems="center"
                justifyContent="end"
                gap={0.5}
              >
                {isCraft(s) && s.status === "ready" && s.external_url && (
                  <Button
                    size="sm"
                    prominence="primary"
                    icon={SvgExternalLink}
                    onClick={() => openCraft(s.external_url as string)}
                  >
                    Open Craft
                  </Button>
                )}
                <Button
                  size="sm"
                  prominence="tertiary"
                  onClick={() => onClose(s.id)}
                >
                  Close
                </Button>
              </Section>
            </Section>
            {isCraft(s) && s.status === "failed" && (
              <Text font="secondary-body" color="text-03">
                {craftFailureMessage(s.failure_reason)}
              </Text>
            )}
          </Section>
        ))}
      </Section>
    </Card>
  );
}
