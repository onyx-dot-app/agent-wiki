"use client";

import { useMemo, useState } from "react";

import {
  InputTypeIn,
  LineItemButton,
  Popover,
  PopoverMenu,
} from "@onyx-ai/opal/components";
import { SvgBubbleText, SvgCheck, SvgHash, SvgUser } from "@onyx-ai/opal/icons";

import {
  ensureSlackDestination,
  getSlackChannels,
  type SlackChannel,
} from "@/lib/slackConnect";
import type { DestinationConfig } from "@/lib/triggers";

interface Props {
  /** The trigger element that opens the picker. */
  children: React.ReactNode;
  configs: DestinationConfig[];
  /** Include the Event log + existing-config entries (trigger modal). */
  includeExisting?: boolean;
  /** Currently selected config id (null = event log). */
  value?: string | null;
  connected: boolean;
  disabled?: boolean;
  /** Called with the picked config id (null = event log). Channel and DM picks
   * find-or-create their destination config first. */
  onPick: (configId: string | null) => void | Promise<void>;
  onError: (message: string) => void;
}

export function SlackDestinationPicker({
  children,
  configs,
  includeExisting = false,
  value,
  connected,
  disabled,
  onPick,
  onError,
}: Props) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [channels, setChannels] = useState<SlackChannel[] | null>(null);
  const [busy, setBusy] = useState(false);

  async function onOpenChange(next: boolean) {
    setOpen(next);
    if (next && connected && channels === null) {
      try {
        setChannels(await getSlackChannels());
      } catch (e) {
        setChannels([]); // drop the loading row; the error line explains
        onError(e instanceof Error ? e.message : "failed to load channels");
      }
    }
  }

  async function pick(configId: string | null) {
    await onPick(configId);
    setOpen(false);
    setSearch("");
  }

  async function pickChannel(ch: SlackChannel) {
    setBusy(true);
    try {
      const { id } = await ensureSlackDestination(configs, {
        kind: "channel",
        id: ch.id,
        name: ch.name,
      });
      await pick(id);
    } catch (e) {
      onError(e instanceof Error ? e.message : "failed to add channel");
    } finally {
      setBusy(false);
    }
  }

  async function pickDm() {
    setBusy(true);
    try {
      const { id } = await ensureSlackDestination(configs, { kind: "dm" });
      await pick(id);
    } catch (e) {
      onError(e instanceof Error ? e.message : "failed to add DM");
    } finally {
      setBusy(false);
    }
  }

  const q = search.trim().toLowerCase();
  const configuredChannelIds = useMemo(
    () => new Set(configs.map((c) => c.config.channel_id).filter(Boolean)),
    [configs],
  );
  const filteredConfigs = includeExisting
    ? configs.filter((c) => !q || c.name.toLowerCase().includes(q))
    : [];
  const filteredChannels = (channels ?? []).filter(
    (ch) =>
      !configuredChannelIds.has(ch.id) &&
      (!q || ch.name.toLowerCase().includes(q)),
  );

  return (
    <Popover open={open} onOpenChange={(v) => void onOpenChange(v)}>
      <Popover.Trigger asChild disabled={disabled}>
        {children}
      </Popover.Trigger>
      <Popover.Content width="fit" align="start" sideOffset={4}>
        <PopoverMenu>
          <InputTypeIn
            searchIcon
            variant="internal"
            placeholder="Search channels…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {includeExisting && !q && (
            <LineItemButton
              icon={SvgBubbleText}
              title="Event log only"
              sizePreset="main-ui"
              variant="body"
              state={value === null ? "selected" : "empty"}
              rightChildren={
                value === null ? <SvgCheck size={16} /> : undefined
              }
              onClick={() => void pick(null)}
            />
          )}
          {filteredConfigs.map((c) => (
            <LineItemButton
              key={c.id}
              icon={c.config.dm ? SvgUser : SvgHash}
              title={c.name}
              sizePreset="main-ui"
              variant="body"
              state={value === c.id ? "selected" : "empty"}
              rightChildren={
                value === c.id ? <SvgCheck size={16} /> : undefined
              }
              onClick={() => void pick(c.id)}
            />
          ))}
          {connected && !configs.some((c) => c.config.dm === true) && (
            <LineItemButton
              icon={SvgUser}
              title="DM me"
              sizePreset="main-ui"
              variant="body"
              state="empty"
              onClick={() => {
                if (!busy) void pickDm();
              }}
            />
          )}
          {connected && channels === null && (
            <LineItemButton
              title="Loading channels…"
              sizePreset="main-ui"
              variant="body"
              state="empty"
              onClick={() => undefined}
            />
          )}
          {filteredChannels.map((ch) => (
            <LineItemButton
              key={ch.id}
              icon={SvgHash}
              title={`${ch.name}${ch.is_private ? " (private)" : ""}`}
              sizePreset="main-ui"
              variant="body"
              state="empty"
              onClick={() => {
                if (!busy) void pickChannel(ch);
              }}
            />
          ))}
        </PopoverMenu>
      </Popover.Content>
    </Popover>
  );
}
