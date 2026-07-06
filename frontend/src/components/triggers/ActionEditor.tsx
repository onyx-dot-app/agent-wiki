"use client";

import { useRef, useState } from "react";

import { LineItemButton, Popover, PopoverMenu } from "@onyx-ai/opal/components";
import {
  SvgActivity,
  SvgChevronDown,
  SvgHash,
  SvgMail,
  SvgSlack,
  SvgTrash,
  SvgUser,
  SvgX,
} from "@onyx-ai/opal/icons";

import { ensureEmailDestination } from "@/lib/emailConnect";
import {
  ensureSlackDestination,
  getSlackChannels,
  type SlackChannel,
} from "@/lib/slackConnect";
import type { DestinationConfig } from "@/lib/triggers";

export type ActionGroupType = "event_log" | "slack" | "email";

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
};

interface Props {
  groups: ActionGroup[];
  onChange: (groups: ActionGroup[]) => void;
  configs: DestinationConfig[];
  refreshConfigs: () => Promise<unknown>;
  slackConnected: boolean;
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
  disabled,
  onError,
}: Props) {
  function patch(key: number, delta: Partial<ActionGroup>) {
    onChange(groups.map((g) => (g.key === key ? { ...g, ...delta } : g)));
  }

  return (
    <div className="flex w-full flex-col gap-3">
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
  disabled,
  onError,
}: RowProps) {
  const [typeOpen, setTypeOpen] = useState(false);
  const meta = TYPE_META[group.type];
  const TypeIcon = meta.icon;

  // Activity Center is offered once across the trigger; Slack only when a
  // connection exists (or this group already targets it, so an edit of an
  // existing Slack trigger stays visible).
  const typeOptions = (
    ["event_log", "slack", "email"] as ActionGroupType[]
  ).filter((t) => {
    if (t === "event_log")
      return group.type === "event_log" || !usedTypes.includes("event_log");
    if (t === "slack") return slackConnected || group.type === "slack";
    return true;
  });

  return (
    <div className="flex w-full flex-col gap-1">
      <div className="flex w-full items-center">
        <span className="flex-1 px-[2px] text-[14px] leading-5 font-semibold text-(--text-04)">
          {label}
        </span>
        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            disabled={disabled}
            className="flex size-7 cursor-pointer items-center justify-center rounded-(--radius-08) border-none bg-transparent p-1 text-(--text-03) hover:bg-(--background-tint-02)"
            aria-label="Remove this action"
          >
            <SvgTrash size={16} />
          </button>
        )}
      </div>

      <Popover open={typeOpen} onOpenChange={setTypeOpen}>
        <Popover.Trigger asChild disabled={disabled}>
          <button
            type="button"
            className="flex h-9 w-full cursor-pointer items-center gap-1 rounded-(--radius-08) border border-(--border-02) bg-(--background-neutral-00) p-[6px] text-left"
          >
            <span className="flex size-6 items-center justify-center p-[2px]">
              <TypeIcon size={18} />
            </span>
            <span className="flex-1 truncate px-[2px] text-[14px] leading-5 font-medium text-(--text-04)">
              {meta.label}
            </span>
            <span className="flex size-6 items-center justify-center p-[2px] text-(--text-03)">
              <SvgChevronDown size={16} />
            </span>
          </button>
        </Popover.Trigger>
        <Popover.Content width="fit" align="start" sideOffset={4}>
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
                  if (t !== group.type) onPatch({ type: t, configIds: [] });
                  setTypeOpen(false);
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

      <textarea
        value={group.message}
        onChange={(e) => onPatch({ message: e.target.value })}
        disabled={disabled}
        placeholder="A notification message to the recipients."
        rows={2}
        className="box-border w-full resize-y rounded-(--radius-08) border border-(--border-02) bg-(--background-tint-00) px-[10px] py-2 text-[14px] leading-5 outline-none placeholder:text-(--text-02) focus:border-(--border-05) focus:shadow-[0_0_0_2px_var(--background-tint-04)]"
      />
    </div>
  );
}

function Chip({
  icon: Icon,
  text,
  onRemove,
  disabled,
}: {
  icon?: typeof SvgHash;
  text: string;
  onRemove: () => void;
  disabled?: boolean;
}) {
  return (
    <span className="flex items-center gap-[2px] rounded-(--radius-08) bg-(--background-tint-02) py-[2px] pr-[2px] pl-1">
      {Icon && (
        <span className="flex size-4 items-center justify-center text-(--text-03)">
          <Icon size={14} />
        </span>
      )}
      <span className="max-w-[200px] truncate px-[2px] text-[14px] leading-5 font-medium text-(--text-04)">
        {text}
      </span>
      <button
        type="button"
        onClick={onRemove}
        disabled={disabled}
        className="flex size-4 cursor-pointer items-center justify-center rounded-(--radius-04) border-none bg-transparent p-[2px] text-(--text-03) hover:bg-(--background-tint-03)"
        aria-label={`Remove ${text}`}
      >
        <SvgX size={12} />
      </button>
    </span>
  );
}

const CHIP_BAR =
  "flex min-h-9 w-full flex-wrap content-center items-center gap-1 rounded-(--radius-08) border border-(--border-02) bg-(--background-neutral-00) p-[6px] focus-within:border-(--border-05) focus-within:shadow-[0_0_0_2px_var(--background-tint-04)]";

const GHOST_INPUT =
  "min-w-[80px] flex-1 border-none bg-transparent px-1 py-[2px] text-[14px] leading-5 outline-none placeholder:text-(--text-02)";

function ToLabel() {
  return (
    <span className="px-[2px] text-[14px] leading-5 font-semibold text-(--text-04)">
      To
    </span>
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
        <p className="m-0 px-[2px] text-[12px] leading-4 text-(--text-03)">
          Connect Slack from the Triggers page to pick channels.
        </p>
      </>
    );
  }

  return (
    <>
      <ToLabel />
      <Popover open={open} onOpenChange={(v) => void onOpenChange(v)}>
        <Popover.Anchor asChild>
          <div ref={anchorRef} className={CHIP_BAR}>
            {selected.map((c) => (
              <Chip
                key={c.id}
                icon={c.config.dm ? SvgUser : SvgHash}
                text={c.name}
                disabled={disabled}
                onRemove={() =>
                  onConfigIds(configIds.filter((id) => id !== c.id))
                }
              />
            ))}
            <input
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
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
              className={GHOST_INPUT}
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
      <div className={CHIP_BAR}>
        {selected.map((c) => (
          <Chip
            key={c.id}
            text={c.verified_at ? c.name : `${c.name} (unverified)`}
            disabled={disabled}
            onRemove={() => onConfigIds(configIds.filter((id) => id !== c.id))}
          />
        ))}
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void add();
            }
          }}
          disabled={disabled}
          placeholder="Add an email"
          className={GHOST_INPUT}
        />
      </div>
    </>
  );
}
