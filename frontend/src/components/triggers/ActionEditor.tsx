"use client";

import { useRef, useState } from "react";

import {
  Button,
  LineItemButton,
  Popover,
  PopoverMenu,
  SelectButton,
  Text,
} from "@onyx-ai/opal/components";
import { SvgOnyxLogo } from "@onyx-ai/opal/logos";
import {
  SvgActivity,
  SvgHash,
  SvgLink,
  SvgMail,
  SvgSlack,
  SvgChevronDown,
  SvgTrash,
  SvgUser,
} from "@onyx-ai/opal/icons";
import { ContentAction } from "@onyx-ai/opal/layouts";

import InputChipField from "@/components/inputs/InputChipField";
import InputTextArea from "@/components/inputs/InputTextArea";
import { ensureEmailDestination } from "@/lib/emailConnect";
import {
  ensureSlackDestination,
  getSlackChannels,
  type SlackChannel,
} from "@/lib/slackConnect";
import { ensureCraftDestination } from "@/lib/craft";
import type { DestinationConfig } from "@/lib/triggers";

export type ActionGroupType =
  | "event_log"
  | "slack"
  | "email"
  | "webhook"
  | "craft";

export interface ActionGroup {
  key: number;
  type: ActionGroupType;
  configIds: string[];
  message: string;
}

const TYPE_META: Record<
  ActionGroupType,
  { label: string; icon: typeof SvgActivity }
> = {
  event_log: { label: "Notification in Activity Center", icon: SvgActivity },
  slack: { label: "Slack", icon: SvgSlack },
  email: { label: "Email", icon: SvgMail },
  webhook: { label: "Webhook", icon: SvgLink },
  craft: { label: "Onyx Craft", icon: SvgOnyxLogo },
};

interface Props {
  groups: ActionGroup[];
  onChange: (groups: ActionGroup[]) => void;
  configs: DestinationConfig[];
  refreshConfigs: () => Promise<unknown>;
  slackConnected: boolean;
  craftConnected: boolean;
  disabled?: boolean;
  onError: (message: string) => void;
}

/** The Then Send / And Send action blocks: per-action destination type,
 * a To chip row for recipients (one destination config per chip), and a
 * per-action message. */
export function ActionEditor({
  groups,
  onChange,
  configs,
  refreshConfigs,
  slackConnected,
  craftConnected,
  disabled,
  onError,
}: Props) {
  function patch(key: number, delta: Partial<ActionGroup>) {
    onChange(groups.map((g) => (g.key === key ? { ...g, ...delta } : g)));
  }

  return (
    <div className="flex w-full flex-col gap-2">
      {groups.map((group, i) => (
        <ActionGroupRow
          key={group.key}
          group={group}
          label={i === 0 ? "Then Send" : "And Send"}
          usedTypes={groups.map((g) => g.type)}
          onPatch={(delta) => patch(group.key, delta)}
          onRemove={
            groups.length > 1
              ? () => onChange(groups.filter((g) => g.key !== group.key))
              : undefined
          }
          configs={configs}
          refreshConfigs={refreshConfigs}
          slackConnected={slackConnected}
          craftConnected={craftConnected}
          disabled={disabled}
          onError={onError}
        />
      ))}
    </div>
  );
}

interface RowProps {
  group: ActionGroup;
  label: string;
  usedTypes: ActionGroupType[];
  onPatch: (delta: Partial<ActionGroup>) => void;
  onRemove?: () => void;
  configs: DestinationConfig[];
  refreshConfigs: () => Promise<unknown>;
  slackConnected: boolean;
  craftConnected: boolean;
  disabled?: boolean;
  onError: (message: string) => void;
}

function ActionGroupRow({
  group,
  label,
  usedTypes,
  onPatch,
  onRemove,
  configs,
  refreshConfigs,
  slackConnected,
  craftConnected,
  disabled,
  onError,
}: RowProps) {
  const [typeOpen, setTypeOpen] = useState(false);
  const meta = TYPE_META[group.type];

  // Activity Center is offered once across the trigger; Slack only when a
  // connection exists (or this group already targets it, so an edit of an
  // existing Slack trigger stays visible).
  const typeOptions = (
    ["event_log", "slack", "email", "webhook", "craft"] as ActionGroupType[]
  ).filter((t) => {
    if (t === "event_log")
      return group.type === "event_log" || !usedTypes.includes("event_log");
    if (t === "slack") return slackConnected || group.type === "slack";
    if (t === "craft") return craftConnected || group.type === "craft";
    if (t === "webhook")
      return (
        group.type === "webhook" || configs.some((c) => c.type === "webhook")
      );
    return true;
  });

  return (
    <div className="group/action flex w-full flex-col gap-1">
      <ContentAction
        title={label}
        sizePreset="main-ui"
        variant="section"
        rightChildren={
          onRemove && (
            /* Hidden at rest per the mock; hover over the action block reveals it. */
            <span className="opacity-0 transition-opacity group-focus-within/action:opacity-100 group-hover/action:opacity-100">
              <Button
                type="button"
                icon={SvgTrash}
                size="sm"
                prominence="tertiary"
                tooltip="Remove this action"
                onClick={onRemove}
                disabled={disabled}
              />
            </span>
          )
        }
      />

      <Popover open={typeOpen} onOpenChange={setTypeOpen}>
        {/* SelectButton draws no border of its own; the slot supplies the
            input border, keeps content left-aligned, and pushes the chevron
            to the right edge. It wraps outside the trigger so the button
            stays the popover's accessible trigger element. */}
        <span className="flex h-9 w-full items-center rounded-(--radius-08) border border-(--border-02) bg-(--background-neutral-00) px-[2px] [&_.opal-select-button]:w-full [&_.opal-select-button]:justify-start [&_.opal-select-button>*:nth-last-child(1)]:ml-auto [&>*]:w-full">
          <Popover.Trigger asChild disabled={disabled}>
            <SelectButton
              icon={meta.icon}
              rightIcon={SvgChevronDown}
              size="sm"
              state="empty"
              width="full"
            >
              {meta.label}
            </SelectButton>
          </Popover.Trigger>
        </span>
        <Popover.Content width="trigger" align="start" sideOffset={4}>
          <PopoverMenu>
            {typeOptions.map((t) => (
              <LineItemButton
                key={t}
                icon={TYPE_META[t].icon}
                title={TYPE_META[t].label}
                sizePreset="main-ui"
                variant="body"
                state={group.type === t ? "selected" : "empty"}
                onClick={() => {
                  setTypeOpen(false);
                  if (t === group.type) return;
                  if (t === "craft") {
                    // Craft has exactly one per-user config. Picking the
                    // type finds-or-creates it and wires the action in one step.
                    void (async () => {
                      try {
                        const id = await ensureCraftDestination(configs);
                        await refreshConfigs();
                        onPatch({ type: t, configIds: [id] });
                      } catch (e) {
                        onError(
                          e instanceof Error
                            ? e.message
                            : "failed to select Onyx Craft",
                        );
                      }
                    })();
                    return;
                  }
                  onPatch({ type: t, configIds: [] });
                }}
              />
            ))}
          </PopoverMenu>
        </Popover.Content>
      </Popover>

      {group.type === "slack" && (
        <SlackToRow
          configIds={group.configIds}
          onConfigIds={(ids) => onPatch({ configIds: ids })}
          configs={configs}
          refreshConfigs={refreshConfigs}
          connected={slackConnected}
          disabled={disabled}
          onError={onError}
        />
      )}
      {group.type === "email" && (
        <EmailToRow
          configIds={group.configIds}
          onConfigIds={(ids) => onPatch({ configIds: ids })}
          configs={configs}
          refreshConfigs={refreshConfigs}
          disabled={disabled}
          onError={onError}
        />
      )}
      {group.type === "craft" && (
        <div className="px-[2px]">
          <Text font="secondary-body" color="text-03">
            Starts an Onyx Craft session for you, seeded with the page and the
            fire.
          </Text>
        </div>
      )}
      {group.type === "webhook" && (
        <WebhookToRow
          configIds={group.configIds}
          onConfigIds={(ids) => onPatch({ configIds: ids })}
          configs={configs}
          disabled={disabled}
        />
      )}

      <InputTextArea
        value={group.message}
        onChange={(e) => onPatch({ message: e.target.value })}
        variant={disabled ? "disabled" : "primary"}
        placeholder={
          group.type === "craft"
            ? "Tell Craft what to build when this fires, e.g. generate a fun image of the page content. Sent to Craft as written."
            : "Describe what the message should say, e.g. summarize what changed and who is affected. The trigger writes the final message from this."
        }
        rows={3}
      />
    </div>
  );
}

function WebhookToRow({
  configIds,
  onConfigIds,
  configs,
  disabled,
}: {
  configIds: string[];
  onConfigIds: (ids: string[]) => void;
  configs: DestinationConfig[];
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const anchorRef = useRef<HTMLDivElement>(null);
  const endpoints = configs.filter((c) => c.type === "webhook");
  const selected = configIds
    .map((id) => endpoints.find((c) => c.id === id))
    .filter((c): c is DestinationConfig => Boolean(c));
  const q = search.trim().toLowerCase();
  const available = endpoints.filter(
    (c) =>
      !configIds.includes(c.id) && (!q || c.name.toLowerCase().includes(q)),
  );

  function add(id: string) {
    onConfigIds([...configIds, id]);
    setSearch("");
  }

  if (endpoints.length === 0) {
    return (
      <>
        <ToLabel />
        <div className="px-[2px]">
          <Text font="secondary-body" color="text-03">
            Add a webhook endpoint in Settings → Connectors to send here.
          </Text>
        </div>
      </>
    );
  }

  return (
    <>
      <ToLabel />
      <Popover open={open} onOpenChange={setOpen}>
        <Popover.Anchor asChild>
          <div ref={anchorRef} className="w-full">
            <InputChipField
              chips={selected.map((c) => ({ id: c.id, label: c.name }))}
              onRemoveChip={(id) =>
                onConfigIds(configIds.filter((x) => x !== id))
              }
              onAdd={() => {
                const first = available[0];
                if (first) add(first.id);
              }}
              value={search}
              onChange={(v) => {
                setSearch(v);
                if (!open) setOpen(true);
              }}
              onFocus={() => setOpen(true)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setOpen(false);
              }}
              disabled={disabled}
              placeholder={
                selected.length ? "Add an endpoint" : "Pick an endpoint"
              }
            />
          </div>
        </Popover.Anchor>
        <Popover.Content
          width="trigger"
          align="start"
          sideOffset={4}
          onOpenAutoFocus={(e) => e.preventDefault()}
          onInteractOutside={(e) => {
            if (anchorRef.current?.contains(e.target as Node))
              e.preventDefault();
          }}
        >
          {available.length === 0 ? (
            <div className="px-3 py-2">
              <Text font="secondary-body" color="text-03">
                {q ? "No matching endpoints." : "All endpoints added."}
              </Text>
            </div>
          ) : (
            <PopoverMenu>
              {available.map((c) => (
                <LineItemButton
                  key={c.id}
                  icon={SvgLink}
                  title={c.name}
                  sizePreset="main-ui"
                  variant="body"
                  state="empty"
                  onClick={() => {
                    add(c.id);
                    setOpen(false);
                  }}
                />
              ))}
            </PopoverMenu>
          )}
        </Popover.Content>
      </Popover>
    </>
  );
}

function ToLabel() {
  return (
    <div className="px-[2px]">
      <Text font="main-ui-action" color="text-04">
        To
      </Text>
    </div>
  );
}

function SlackToRow({
  configIds,
  onConfigIds,
  configs,
  refreshConfigs,
  connected,
  disabled,
  onError,
}: {
  configIds: string[];
  onConfigIds: (ids: string[]) => void;
  configs: DestinationConfig[];
  refreshConfigs: () => Promise<unknown>;
  connected: boolean;
  disabled?: boolean;
  onError: (message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [channels, setChannels] = useState<SlackChannel[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const anchorRef = useRef<HTMLDivElement>(null);

  const selected = configIds
    .map((id) => configs.find((c) => c.id === id))
    .filter((c): c is DestinationConfig => Boolean(c));
  const selectedChannelIds = new Set(
    selected.map((c) => c.config.channel_id).filter(Boolean),
  );
  const hasDm = selected.some((c) => c.config.dm === true);

  async function onOpenChange(next: boolean) {
    setOpen(next);
    // channels stays null on failure so the next open retries the fetch.
    if (next && connected && channels === null && !loading) {
      setLoading(true);
      try {
        setChannels(await getSlackChannels());
      } catch (e) {
        onError(e instanceof Error ? e.message : "failed to load channels");
      } finally {
        setLoading(false);
      }
    }
  }

  async function pick(target: { kind: "dm" } | SlackChannel) {
    if (busy) return;
    setBusy(true);
    try {
      const { id } =
        "kind" in target
          ? await ensureSlackDestination(configs, { kind: "dm" })
          : await ensureSlackDestination(configs, {
              kind: "channel",
              id: target.id,
              name: target.name,
            });
      await refreshConfigs();
      if (!configIds.includes(id)) onConfigIds([...configIds, id]);
      // Stay open so several recipients can be added in one pass.
      setSearch("");
    } catch (e) {
      onError(e instanceof Error ? e.message : "failed to add recipient");
    } finally {
      setBusy(false);
    }
  }

  const q = search.trim().toLowerCase();
  const filtered = (channels ?? []).filter(
    (ch) =>
      !selectedChannelIds.has(ch.id) &&
      (!q || ch.name.toLowerCase().includes(q)),
  );

  if (!connected) {
    return (
      <>
        <ToLabel />
        <div className="px-[2px]">
          <Text font="secondary-body" color="text-03">
            Connect Slack from the Triggers page to pick channels.
          </Text>
        </div>
      </>
    );
  }

  return (
    <>
      <ToLabel />
      <Popover open={open} onOpenChange={(v) => void onOpenChange(v)}>
        <Popover.Anchor asChild>
          <div ref={anchorRef} className="w-full">
            <InputChipField
              chips={selected.map((c) => ({ id: c.id, label: c.name }))}
              onRemoveChip={(id) =>
                onConfigIds(configIds.filter((x) => x !== id))
              }
              onAdd={() => {
                const first = filtered[0];
                if (first) void pick(first);
              }}
              value={search}
              onChange={(v) => {
                setSearch(v);
                if (!open) void onOpenChange(true);
              }}
              onFocus={() => {
                if (!open) void onOpenChange(true);
              }}
              onKeyDown={(e) => {
                if (e.key === "Escape") setOpen(false);
              }}
              disabled={disabled}
              placeholder={
                selected.length ? "Add a channel" : "Add a channel or DM"
              }
            />
          </div>
        </Popover.Anchor>
        <Popover.Content
          width="trigger"
          align="start"
          sideOffset={4}
          onOpenAutoFocus={(e) => e.preventDefault()}
          onInteractOutside={(e) => {
            // Clicking back into the chip bar (the anchor) is not "outside".
            if (anchorRef.current?.contains(e.target as Node))
              e.preventDefault();
          }}
        >
          <PopoverMenu>
            {!hasDm && !q && (
              <LineItemButton
                icon={SvgUser}
                title="DM me"
                sizePreset="main-ui"
                variant="body"
                state="empty"
                onClick={() => void pick({ kind: "dm" })}
              />
            )}
            {loading && (
              <LineItemButton
                title="Loading channels…"
                sizePreset="main-ui"
                variant="body"
                state="empty"
                onClick={() => undefined}
              />
            )}
            {filtered.map((ch) => (
              <LineItemButton
                key={ch.id}
                icon={SvgHash}
                title={`${ch.name}${ch.is_private ? " (private)" : ""}`}
                sizePreset="main-ui"
                variant="body"
                state="empty"
                onClick={() => void pick(ch)}
              />
            ))}
          </PopoverMenu>
        </Popover.Content>
      </Popover>
    </>
  );
}

function EmailToRow({
  configIds,
  onConfigIds,
  configs,
  refreshConfigs,
  disabled,
  onError,
}: {
  configIds: string[];
  onConfigIds: (ids: string[]) => void;
  configs: DestinationConfig[];
  refreshConfigs: () => Promise<unknown>;
  disabled?: boolean;
  onError: (message: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const selected = configIds
    .map((id) => configs.find((c) => c.id === id))
    .filter((c): c is DestinationConfig => Boolean(c));

  async function add() {
    if (busy) return;
    const address = draft.trim();
    if (!address) return;
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
      await refreshConfigs();
      if (!configIds.includes(id)) onConfigIds([...configIds, id]);
      // The input stays mounted and focused so more addresses can follow.
      setDraft("");
      if (verificationError) onError(verificationError);
    } catch (e) {
      onError(e instanceof Error ? e.message : "failed to add address");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <ToLabel />
      <InputChipField
        chips={selected.map((c) => ({
          id: c.id,
          label: c.verified_at ? c.name : `${c.name} (unverified)`,
          error: !c.verified_at,
        }))}
        onRemoveChip={(id) => onConfigIds(configIds.filter((x) => x !== id))}
        onAdd={() => void add()}
        value={draft}
        onChange={setDraft}
        disabled={disabled || busy}
        placeholder="Add an email"
      />
    </>
  );
}
