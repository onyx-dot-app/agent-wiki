"use client";

// Watch mode of the floating wiki toolbar (Figma 2348:387907): describe a
// condition and name the action to fire. Creates a delta trigger scoped to
// whatever the toolbar has attached.
import { useState } from "react";

import {
  Button,
  Divider,
  InputSelect,
  InputTextArea,
  Tabs,
  Text,
} from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";
import { SvgActivity } from "@onyx-ai/opal/icons";
import { markdown } from "@onyx-ai/opal/utils";

import {
  Composer,
  type ToolbarContext,
} from "@/components/wiki/toolbar/chatParts";
import { useRemovableToolbarContext } from "@/components/wiki/toolbar/useToolbarContext";
import { createTrigger, useDestinationConfigs } from "@/lib/triggers";
import { formatChatError } from "@/lib/chatState";

const CONDITION_PLACEHOLDER =
  'Describe what changes to watch for…\ne.g. "any todo item is updated", "a new section is added"';

const MESSAGE_PLACEHOLDER =
  'Describe what message to send…\ne.g. "a summary of what changed and any action items"';

/** The Activity Center is the built-in destination: a null config id means
 *  the notification lands in-app rather than on a configured channel. */
const ACTIVITY_CENTER = "activity-center";

interface ToolbarWatchProps {
  context?: ToolbarContext | null;
}

export function ToolbarWatch({ context }: ToolbarWatchProps) {
  const { configs } = useDestinationConfigs();
  const [condition, setCondition] = useState("");
  const [message, setMessage] = useState("");
  const [destination, setDestination] = useState(ACTIVITY_CENTER);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { contexts, removeContext, attachContext } =
    useRemovableToolbarContext(context);
  // Watch scopes and Launch payloads take one page, the first chip wins.
  const effectiveContext = contexts[0] ?? null;
  const scopePath = effectiveContext?.path ?? "";
  const canSave =
    condition.trim().length > 0 && message.trim().length > 0 && !saving;

  const destinationName =
    configs?.find((c) => c.id === destination)?.name ??
    "Notification in Activity Center";

  function submit() {
    if (!canSave) return;
    setSaving(true);
    setError(null);
    void (async () => {
      try {
        const scope =
          effectiveContext &&
          effectiveContext.startLine != null &&
          effectiveContext.endLine != null
            ? [
                {
                  path: scopePath,
                  start_line: effectiveContext.startLine,
                  end_line: effectiveContext.endLine,
                },
              ]
            : undefined;
        await createTrigger({
          scope_path: scopePath || "/",
          scopes: scope,
          nl_description: condition.trim(),
          kind: "delta",
          actions: [
            {
              destination_config_id:
                destination === ACTIVITY_CENTER ? null : destination,
              message: message.trim(),
            },
          ],
        });
        setCondition("");
        setMessage("");
      } catch (e) {
        setError(formatChatError(e));
      } finally {
        setSaving(false);
      }
    })();
  }

  return (
    <Section gap={0} padding={0} height="fit" alignItems="stretch">
      {/* raw-ok: horizontal-only inset. Section numeric padding is uniform and silences px- utilities. */}
      <div className="w-full px-1">
        <Section gap={0.5} padding={0} height="fit" alignItems="stretch">
          <Composer
            value={condition}
            onChange={setCondition}
            onSubmit={submit}
            placeholder={CONDITION_PLACEHOLDER}
            contexts={contexts}
            onRemoveContext={removeContext}
            onAttachContext={attachContext}
            hideSend
            rows={2}
          />
          {/* raw-ok: helper text inset, px-2.5 plus the parent px-1 lands the mock's 14px. */}
          <div className="w-full px-2.5">
            <Text font="secondary-body" color="text-03">
              Add sections or entire pages to watch.
            </Text>
          </div>
        </Section>
      </div>

      <Section gap={0.75} padding={0.75} height="fit" alignItems="stretch">
        <Divider />
        {/* Schedules need a cron and timezone this form doesn't collect,
            so that tab stays disabled. */}
        <Section
          gap={0}
          padding={0}
          alignItems="stretch"
          className="chat-form-tabs"
        >
          <Tabs variant="contained" defaultValue="delta">
            <Tabs.List>
              <Tabs.Trigger value="delta">Run on Wiki Updates</Tabs.Trigger>
              <Tabs.Trigger value="schedule" disabled>
                Run on a Schedule
              </Tabs.Trigger>
            </Tabs.List>
          </Tabs>
        </Section>

        <Section gap={0.25} padding={0} height="fit" alignItems="stretch">
          <Text font="main-ui-action" color="text-04">
            Then Trigger
          </Text>
          <InputSelect value={destination} onValueChange={setDestination}>
            <InputSelect.Trigger placeholder={destinationName} />
            <InputSelect.Content>
              <InputSelect.Item
                value={ACTIVITY_CENTER}
                icon={SvgActivity}
                // The in-app destination always exists, so it needs no
                // configuration row behind it.
              >
                Notification in Activity Center
              </InputSelect.Item>
              {(configs ?? []).map((c) => (
                <InputSelect.Item key={c.id} value={c.id}>
                  {c.name}
                </InputSelect.Item>
              ))}
            </InputSelect.Content>
          </InputSelect>
          <InputTextArea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder={MESSAGE_PLACEHOLDER}
            rows={2}
            autoResize
            maxRows={4}
          />
        </Section>
      </Section>

      {/* Full-bleed white footer band (mock 2348:387966), the panel's
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
            `Messages will be sent to **${destinationName}** when the watched content matches your condition.`,
          )}
        </Text>
        <Button
          variant="action"
          prominence="primary"
          size="lg"
          disabled={!canSave}
          onClick={submit}
        >
          Start Watching
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
