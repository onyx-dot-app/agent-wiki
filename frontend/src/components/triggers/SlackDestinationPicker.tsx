"use client";

import { useMemo, useState } from "react";

import {
  InputTypeIn,
  LineItemButton,
  Popover,
  PopoverMenu,
} from "@onyx-ai/opal/components";
import {
  SvgBubbleText,
  SvgCheck,
  SvgHash,
  SvgMail,
  SvgUser,
} from "@onyx-ai/opal/icons";

import { ensureEmailDestination } from "@/lib/emailConnect";
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
  const [loadingChannels, setLoadingChannels] = useState(false);
  const [busy, setBusy] = useState(false);
  const [addingEmail, setAddingEmail] = useState(false);
  const [emailInput, setEmailInput] = useState("");

  async function onOpenChange(next: boolean) {
    setOpen(next);
    // channels stays null on failure so the next open retries the fetch.
    if (next && connected && channels === null && !loadingChannels) {
      setLoadingChannels(true);
      try {
        setChannels(await getSlackChannels());
      } catch (e) {
        onError(e instanceof Error ? e.message : "failed to load channels");
      } finally {
        setLoadingChannels(false);
      }
    }
  }

  async function pick(configId: string | null) {
    await onPick(configId);
    setOpen(false);
    setSearch("");
    setAddingEmail(false);
    setEmailInput("");
  }

  async function pickEmail() {
    const address = emailInput.trim();
    if (!address.includes("@")) {
      onError("enter a valid email address");
      return;
    }
    setBusy(true);
    try {
      const { id, verificationError } = await ensureEmailDestination(
        configs,
        address,
      );
      await pick(id);
      if (verificationError) onError(verificationError);
    } catch (e) {
      onError(e instanceof Error ? e.message : "failed to add address");
    } finally {
      setBusy(false);
    }
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
            placeholder="Search…"
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
              icon={
                c.type === "email" ? SvgMail : c.config.dm ? SvgUser : SvgHash
              }
              title={
                c.type === "email" && !c.verified_at
                  ? `${c.name} (unverified)`
                  : c.name
              }
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
          {!addingEmail && (
            <LineItemButton
              icon={SvgMail}
              title="Email an address…"
              sizePreset="main-ui"
              variant="body"
              state="empty"
              onClick={() => setAddingEmail(true)}
            />
          )}
          {addingEmail && (
            <InputTypeIn
              autoFocus
              variant="internal"
              placeholder="name@example.com — Enter to add"
              value={emailInput}
              onChange={(e) => setEmailInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !busy) void pickEmail();
                if (e.key === "Escape") setAddingEmail(false);
              }}
            />
          )}
          {loadingChannels && (
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
