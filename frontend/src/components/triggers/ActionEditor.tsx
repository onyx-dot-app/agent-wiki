"use client";

import { useRef, useState } from "react";

import {
  Button,
  FilterButton,
  InputTypeIn,
  LineItemButton,
  Popover,
  PopoverMenu,
  SelectButton,
  Text,
} from "@onyx-ai/opal/components";
import {
  SvgActivity,
  SvgHash,
  SvgMail,
  SvgSlack,
  SvgTrash,
  SvgUser,
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
      <div className="flex w-full items-center px-[2px]">
        <div className="flex-1">
          <Text font="main-ui-action" color="text-04">
            {label}
          </Text>
        </div>
        {onRemove && (
          <Button
            type="button"
            icon={SvgTrash}
            size="sm"
            tooltip="Remove this action"
            onClick={onRemove}
            disabled={disabled}
          />
        )}
      </div>

      <Popover open={typeOpen} onOpenChange={setTypeOpen}>
        <Popover.Trigger asChild disabled={disabled}>
          <SelectButton icon={meta.icon} size="sm" state="empty" width="full">
            {meta.label}
          </SelectButton>
        </Popover.Trigger>
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

/** Input-shaped chip container — a composite Opal doesn't provide; the chips
 * and inline input inside it are library components. */
const CHIP_BAR =
  "flex min-h-9 w-full flex-wrap content-center items-center gap-1 rounded-(--radius-08) border border-(--border-02) bg-(--background-neutral-00) p-[6px] focus-within:border-(--border-05) focus-within:shadow-[0_0_0_2px_var(--background-tint-04)]";

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
          <div ref={anchorRef} className={CHIP_BAR}>
            {selected.map((c) => (
              <FilterButton
                key={c.id}
                icon={c.config.dm ? SvgUser : SvgHash}
                active
                onClear={() =>
                  onConfigIds(configIds.filter((id) => id !== c.id))
                }
                disabled={disabled}
              >
                {c.name}
              </FilterButton>
            ))}
            <div className="min-w-[120px] flex-1">
              <InputTypeIn
                variant="internal"
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
                placeholder={
                  selected.length ? "Add a channel" : "Add a channel or DM"
                }
              />
            </div>
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
          <FilterButton
            key={c.id}
            icon={SvgMail}
            active
            onClear={() => onConfigIds(configIds.filter((id) => id !== c.id))}
            disabled={disabled}
            tooltip={c.verified_at ? undefined : "Not verified yet"}
          >
            {c.verified_at ? c.name : `${c.name} (unverified)`}
          </FilterButton>
        ))}
        <div className="min-w-[120px] flex-1">
          <InputTypeIn
            variant="internal"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void add();
              }
            }}
            placeholder="Add an email"
          />
        </div>
      </div>
    </>
  );
}
