"use client";

import { useState } from "react";

import { Button, InputTypeIn } from "@onyx-ai/opal/components";
import { SvgLink, SvgX } from "@onyx-ai/opal/icons";
import { InputErrorText } from "@onyx-ai/opal/layouts";

import { SvgSend } from "@/components/icons/SvgSend";
import {
  ConfigRowCard,
  ConnectorModalShell,
  CountDivider,
  MiniActionButton,
} from "@/components/settings/ConnectorModal";
import {
  createDestinationConfig,
  deleteDestinationConfig,
  sendTestEvent,
  type DestinationConfig,
} from "@/lib/triggers";

/** Short label for a webhook endpoint: its host, falling back to the raw URL. */
function hostLabel(url: string): string {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}

/** Register/manage webhook endpoints. Each is a `destination_configs` row of
 * type webhook with a URL, an optional routing tag, and a signing secret
 * (server-minted when the creator supplies none). "Test" POSTs a sample event
 * so a receiver can learn the shape. */
export function WebhookModal({
  configs,
  refresh,
  onClose,
}: {
  configs: DestinationConfig[];
  refresh: () => Promise<unknown>;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [tag, setTag] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [tested, setTested] = useState<Set<string>>(new Set());

  async function onAdd() {
    const target = url.trim();
    if (!target || busy) return;
    setBusy(true);
    setError(null);
    try {
      const routingTag = tag.trim();
      await createDestinationConfig({
        type: "webhook",
        name: name.trim() || hostLabel(target),
        config: routingTag
          ? { url: target, routing_tag: routingTag }
          : { url: target },
      });
      await refresh();
      setName("");
      setUrl("");
      setTag("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to add endpoint");
    } finally {
      setBusy(false);
    }
  }

  async function onTest(c: DestinationConfig) {
    setError(null);
    setTesting(c.id);
    try {
      await sendTestEvent(c.id);
      setTested((cur) => new Set(cur).add(c.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "test send failed");
    } finally {
      setTesting((cur) => (cur === c.id ? null : cur));
    }
  }

  async function onDelete(c: DestinationConfig) {
    setError(null);
    try {
      await deleteDestinationConfig(c.id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to remove");
    }
  }

  return (
    <ConnectorModalShell
      icon={SvgLink}
      title="Webhooks"
      description="POST wiki updates to Zapier, n8n, Make, or any HTTP endpoint."
      onClose={onClose}
    >
      <InputTypeIn
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Name (e.g. Slack #general)"
      />
      <InputTypeIn
        value={url}
        onChange={(e) => {
          setUrl(e.target.value);
          setError(null);
        }}
        placeholder="https://hooks.zapier.com/hooks/catch/…"
      />
      <div className="flex w-full items-center gap-1">
        <div className="min-w-0 flex-1">
          <InputTypeIn
            value={tag}
            onChange={(e) => setTag(e.target.value)}
            placeholder="Routing tag (optional)"
          />
        </div>
        <Button
          type="button"
          variant="action"
          disabled={busy || !url.trim()}
          onClick={() => void onAdd()}
        >
          Add
        </Button>
      </div>

      {error && <InputErrorText type="error">{error}</InputErrorText>}

      <div className="flex w-full flex-col gap-1">
        {configs.map((c) => {
          const endpoint = String(c.config.url ?? c.name);
          const routingTag =
            typeof c.config.routing_tag === "string"
              ? c.config.routing_tag
              : null;
          return (
            <ConfigRowCard
              key={c.id}
              icon={SvgLink}
              title={c.name}
              description={
                routingTag ? `${endpoint} · ${routingTag}` : endpoint
              }
            >
              <MiniActionButton
                icon={SvgSend}
                disabled={testing === c.id}
                onClick={() => void onTest(c)}
              >
                {tested.has(c.id) ? "Sent" : "Test"}
              </MiniActionButton>
              <Button
                type="button"
                icon={SvgX}
                size="sm"
                prominence="tertiary"
                tooltip="Remove endpoint"
                onClick={() => void onDelete(c)}
              />
            </ConfigRowCard>
          );
        })}
        <CountDivider count={configs.length} noun="Webhook" />
      </div>
    </ConnectorModalShell>
  );
}
