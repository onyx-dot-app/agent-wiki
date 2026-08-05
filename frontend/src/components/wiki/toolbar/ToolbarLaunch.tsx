"use client";

// Launch mode of the floating wiki toolbar (Figma 2311:98723): a message
// for the agent plus the agent to hand it to. Launching leaves the app,
// so the URI the backend mints is followed directly.
import { useState } from "react";

import { Button, Divider, InputSelect, Text } from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";
import { SvgClaude, SvgOnyxLogo, SvgOpenai } from "@onyx-ai/opal/logos";
import { markdown } from "@onyx-ai/opal/utils";

import {
  Composer,
  type ToolbarContext,
} from "@/components/wiki/toolbar/chatParts";
import { useRemovableToolbarContext } from "@/components/wiki/toolbar/useToolbarContext";
import { formatChatError } from "@/lib/chatState";
import { launch, useLauncherCatalog } from "@/lib/launchers";

function agentIcon(id: string) {
  if (id.includes("claude")) return SvgClaude;
  if (id.includes("codex") || id.includes("openai")) return SvgOpenai;
  return SvgOnyxLogo;
}

interface ToolbarLaunchProps {
  context?: ToolbarContext | null;
}

export function ToolbarLaunch({ context }: ToolbarLaunchProps) {
  const [message, setMessage] = useState("");
  const [agentId, setAgentId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { contexts, removeContext, attachContext } =
    useRemovableToolbarContext(context);
  // Watch scopes and Launch payloads take one page, the first chip wins.
  const effectiveContext = contexts[0] ?? null;
  const wikiPath = effectiveContext?.path ?? null;
  const { launchers } = useLauncherCatalog({ wikiPath });
  const available = launchers.filter((l) => l.available_for_launch);
  const selected =
    available.find((l) => l.id === agentId) ?? available[0] ?? null;
  const canLaunch = !!selected && message.trim().length > 0 && !busy;

  function submit() {
    if (!canLaunch || !selected) return;
    setBusy(true);
    setError(null);
    void (async () => {
      try {
        const res = await launch({
          tool_id: selected.id,
          // Removing the context chip launches without page context.
          wiki_path: wikiPath,
          working_dir: selected.default_working_dir,
          message: message.trim(),
        });
        window.location.href = res.uri;
      } catch (e) {
        setError(formatChatError(e));
        setBusy(false);
      }
    })();
  }

  return (
    <Section gap={0} padding={0} height="fit" alignItems="stretch">
      {/* raw-ok: horizontal-only inset. Section numeric padding is uniform and silences px- utilities. */}
      <div className="w-full px-1">
        <Section gap={0.5} padding={0} height="fit" alignItems="stretch">
          <Composer
            value={message}
            onChange={setMessage}
            onSubmit={submit}
            placeholder="Add or attach more details and tasks for the agent…"
            contexts={contexts}
            onRemoveContext={removeContext}
            onAttachContext={attachContext}
            hideSend
          />
          {/* raw-ok: helper text inset, px-2.5 plus the parent px-1 lands the mock's 14px. */}
          <div className="w-full px-2.5">
            <Text font="secondary-body" color="text-03">
              Attach sections or entire pages to your message.
            </Text>
          </div>
        </Section>
      </div>

      <Section gap={0.75} padding={0.75} height="fit" alignItems="stretch">
        <Divider />
        <Section gap={0.25} padding={0} height="fit" alignItems="stretch">
          <Text font="main-ui-action" color="text-04">
            Agent to Launch
          </Text>
          <InputSelect
            value={selected?.id ?? ""}
            onValueChange={setAgentId}
            disabled={available.length === 0}
          >
            <InputSelect.Trigger
              placeholder={
                available.length === 0
                  ? "No agents set up yet"
                  : (selected?.name ?? "Pick an agent")
              }
            />
            <InputSelect.Content>
              {available.map((l) => (
                <InputSelect.Item
                  key={l.id}
                  value={l.id}
                  icon={agentIcon(l.id)}
                  description={l.tagline}
                >
                  {l.name}
                </InputSelect.Item>
              ))}
            </InputSelect.Content>
          </InputSelect>
        </Section>
      </Section>

      {/* Full-bleed white footer band (mock 2311:98778), the panel's
          overflow clip rounds its bottom corners. */}
      <Section
        flexDirection="row"
        justifyContent="between"
        alignItems="center"
        gap={0.5}
        padding={0.75}
        height="fit"
        className="bg-background-tint-00"
      >
        <Text font="secondary-body" color="text-03">
          {markdown(
            `This will launch a session in **${selected?.name ?? "an agent"}** with your message.`,
          )}
        </Text>
        <Button
          variant="action"
          prominence="primary"
          size="lg"
          disabled={!canLaunch}
          onClick={submit}
        >
          Launch
        </Button>
      </Section>
      {error && (
        <Section gap={0} padding={0.75} height="fit" alignItems="start">
          <Text font="secondary-body" color="status-error-05">
            {error}
          </Text>
        </Section>
      )}
    </Section>
  );
}
