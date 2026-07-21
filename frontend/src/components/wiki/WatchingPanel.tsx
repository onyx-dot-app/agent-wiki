"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";

import {
  Button,
  Divider,
  EndOfList,
  Switch,
  Text,
} from "@onyx-ai/opal/components";
import {
  SvgActivity,
  SvgChevronDown,
  SvgChevronUp,
  SvgDocFile,
  SvgExpand,
  SvgFold,
  SvgMail,
  SvgPlus,
  SvgSettings,
  SvgShareWebhook,
  SvgWorkflow,
} from "@onyx-ai/opal/icons";
import { SvgOnyxLogo, SvgSlack } from "@onyx-ai/opal/logos";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";

import { Section } from "@onyx-ai/opal/layouts";

import { PanelSearchField } from "@/components/wiki/PanelSearch";
import { toast } from "@/hooks/useToast";
import { pageTitle } from "@/lib/wiki/utils";
import { relativeTime } from "@/lib/time";
import {
  getTriggerFires,
  updateTrigger,
  useDestinationConfigs,
  useTriggers,
  type DestinationConfig,
  type Trigger,
  type TriggerFire,
  type TriggerScope,
} from "@/lib/triggers";

/** Faithful mirror of the backend's ``_in_scope`` (app/triggers/diff.py):
 * empty scope = whole wiki, else exact match or directory prefix. Stored
 * scopes arrive normalized (no leading slash, root collapsed to ""). */
function scopeCovers(scope: string, path: string): boolean {
  if (!scope) return true;
  if (path === scope) return true;
  return path.startsWith(`${scope.replace(/\/+$/, "")}/`);
}

// Destination slugs from app/triggers/destinations.py. A null config id is
// the always-present Activity Center (event_log) destination.
const DEST_ICON: Record<string, IconFunctionComponent> = {
  event_log: SvgActivity,
  email: SvgMail,
  slack: SvgSlack,
  webhook: SvgShareWebhook,
  craft: SvgOnyxLogo,
};

const DEST_LABEL: Record<string, string> = {
  event_log: "Activity Center",
  email: "Email",
  slack: "Slack",
  webhook: "Webhook",
  craft: "Craft",
};

interface DestView {
  type: string;
  label: string;
}

function destView(
  configId: string | null,
  configs: DestinationConfig[],
): DestView {
  const cfg = configId ? configs.find((c) => c.id === configId) : null;
  if (!cfg) return { type: "event_log", label: DEST_LABEL.event_log };
  return {
    type: cfg.type,
    label: cfg.name || (DEST_LABEL[cfg.type] ?? cfg.type),
  };
}

function scopeLabel(s: TriggerScope): string {
  const title = s.path ? pageTitle(s.path) : "Entire wiki";
  return s.start_line != null && s.end_line != null
    ? `${title} (line ${s.start_line} - ${s.end_line})`
    : title;
}

interface Props {
  path: string;
  onNew: () => void;
  onEdit: (trigger: Trigger) => void;
}

/**
 * Watching tab body (mock 1899:296187): search + new/expand-all actions,
 * then one raised card per watcher on this page: scope chips, destination
 * avatars, active toggle, IF/Then/message lines, gear, and the latest run
 * with an expandable detail. Inactive watchers sit below a titled divider.
 */
export function WatchingPanel({ path, onNew, onEdit }: Props) {
  const { triggers, error, refresh } = useTriggers();
  const { configs } = useDestinationConfigs();
  const { data: firesData } = useSWR("/triggers/fires?per_trigger=1", () =>
    getTriggerFires({ perTrigger: 1 }),
  );
  const [search, setSearch] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [openIds, setOpenIds] = useState<ReadonlySet<string>>(new Set());

  const latestFire = useMemo(() => {
    const m = new Map<string, TriggerFire>();
    for (const f of firesData ?? []) {
      if (!m.has(f.trigger_id)) m.set(f.trigger_id, f);
    }
    return m;
  }, [firesData]);

  const q = search.trim().toLowerCase();
  const visible = triggers.filter(
    (t) =>
      scopeCovers(t.scope_path, path) &&
      (!q ||
        t.nl_description.toLowerCase().includes(q) ||
        t.actions.some(
          (a) =>
            (a.message ?? "").toLowerCase().includes(q) ||
            destView(a.destination_config_id, configs)
              .label.toLowerCase()
              .includes(q),
        )),
  );
  const active = visible.filter((t) => t.enabled);
  const inactive = visible.filter((t) => !t.enabled);
  const anyClosed = visible.some(
    (t) => latestFire.has(t.id) && !openIds.has(t.id),
  );

  function toggleAllDetails() {
    setOpenIds(
      anyClosed ? new Set(visible.map((t) => t.id)) : new Set<string>(),
    );
  }

  async function onToggle(t: Trigger) {
    setBusyId(t.id);
    try {
      const updated = await updateTrigger(t.id, { enabled: !t.enabled });
      await refresh(
        (cur) => ({
          triggers: (cur?.triggers ?? []).map((x) =>
            x.id === t.id ? updated : x,
          ),
        }),
        { revalidate: true },
      );
    } catch {
      // The switch snaps back on its own (state never changed server-side),
      // but the user needs to know why.
      toast.error("Couldn't update the watcher.");
    } finally {
      setBusyId(null);
    }
  }

  const card = (t: Trigger, muted = false) => (
    <WatcherCard
      key={t.id}
      trigger={t}
      configs={configs}
      muted={muted}
      fire={latestFire.get(t.id) ?? null}
      busy={busyId === t.id}
      detailsOpen={openIds.has(t.id)}
      onToggleDetails={() =>
        setOpenIds((cur) => {
          const next = new Set(cur);
          if (next.has(t.id)) next.delete(t.id);
          else next.add(t.id);
          return next;
        })
      }
      onToggle={() => void onToggle(t)}
      onEdit={() => onEdit(t)}
    />
  );

  return (
    <Section
      justifyContent="start"
      alignItems="stretch"
      height="auto"
      gap={0}
      padding={0.25}
      className="min-h-0 flex-1 overflow-clip rounded-(--radius-12) border border-(--border-01) bg-(--background-tint-01)"
    >
      <div className="flex shrink-0 items-center gap-1">
        <PanelSearchField
          value={search}
          onChange={setSearch}
          placeholder="Search watching…"
        />
        <div className="flex shrink-0 items-center gap-1 p-1">
          <Button
            icon={SvgPlus}
            size="md"
            prominence="tertiary"
            tooltip="New watcher"
            onClick={onNew}
          />
          <Button
            icon={anyClosed ? SvgExpand : SvgFold}
            size="md"
            prominence="tertiary"
            tooltip={anyClosed ? "Expand run details" : "Collapse run details"}
            onClick={toggleAllDetails}
          />
        </div>
      </div>
      <Divider />
      <Section
        justifyContent="start"
        alignItems="stretch"
        height="auto"
        gap={0.25}
        className="scroll-y-hidden min-h-0 flex-1 overflow-y-auto"
      >
        {error && (
          <Text font="secondary-body" color="text-03">
            {error.message}
          </Text>
        )}
        {!error && visible.length === 0 && (
          <div className="p-3">
            <Text font="secondary-body" color="text-03">
              {q ? "No watchers match." : "Nothing is watching this page yet."}
            </Text>
          </div>
        )}
        {active.map((t) => card(t))}
        {inactive.length > 0 && (
          <div className="py-1">
            <Divider title="Inactive" />
          </div>
        )}
        {inactive.map((t) => card(t, true))}
        {visible.length > 0 && (
          <div className="px-4 py-2">
            <EndOfList title={`${visible.length} Watching`} />
          </div>
        )}
      </Section>
    </Section>
  );
}

interface WatcherCardProps {
  trigger: Trigger;
  configs: DestinationConfig[];
  /** Inactive cards sit flush on the panel tint instead of raised white. */
  muted: boolean;
  fire: TriggerFire | null;
  busy: boolean;
  detailsOpen: boolean;
  onToggleDetails: () => void;
  onToggle: () => void;
  onEdit: () => void;
}

/** One watcher (mock 1899:296629 collapsed / 1899:296720 expanded). */
function WatcherCard({
  trigger,
  configs,
  muted,
  fire,
  busy,
  detailsOpen,
  onToggleDetails,
  onToggle,
  onEdit,
}: WatcherCardProps) {
  const scopes: TriggerScope[] = trigger.scopes.length
    ? trigger.scopes
    : [{ path: trigger.scope_path }];
  const dests = trigger.actions.map((a) =>
    destView(a.destination_config_id, configs),
  );
  // One avatar per destination type, the Then line names every target.
  const destTypes = [...new Set(dests.map((d) => d.type))];
  const destNames = [...new Set(dests.map((d) => d.label))].join(", ");
  const message = trigger.actions.find((a) => a.message)?.message ?? null;
  const fireDest = fire
    ? destView(fire.destination_config_id, configs).label
    : null;

  return (
    <Section
      justifyContent="start"
      alignItems="stretch"
      height="fit"
      gap={0}
      padding={0.25}
      className={`shrink-0 overflow-clip rounded-(--radius-12) ${
        muted
          ? "bg-(--background-tint-01)"
          : "bg-(--background-tint-00) shadow-(--shadow-box-00)"
      } hover:shadow-(--shadow-box-01)`}
    >
      <div className="flex w-full items-start p-[2px]">
        <div className="flex min-w-0 flex-1 items-center gap-1 p-[2px]">
          {scopes.map((s, i) => (
            <span
              key={`${s.path}:${i}`}
              className="flex min-w-0 shrink items-center overflow-hidden rounded-(--radius-04) bg-(--background-tint-02) p-[2px]"
            >
              <span className="flex size-4 shrink-0 items-center justify-center text-(--text-03)">
                <SvgDocFile size={12} />
              </span>
              <span className="max-w-40 truncate px-[2px] text-[12px] leading-4 whitespace-nowrap text-(--text-03)">
                {scopeLabel(s)}
              </span>
            </span>
          ))}
          <span className="flex size-4 shrink-0 items-center justify-center text-(--text-03)">
            <SvgWorkflow size={12} />
          </span>
          <span className="flex shrink-0 items-center">
            {destTypes.map((type, i) => {
              const Icon = DEST_ICON[type] ?? SvgActivity;
              return (
                <span
                  key={type}
                  className={`box-border flex size-5 items-center justify-center overflow-hidden rounded-full border border-(--border-01) bg-(--background-tint-00) ${
                    i > 0 ? "-ml-1" : ""
                  }`}
                >
                  <Icon size={16} />
                </span>
              );
            })}
          </span>
        </div>
        <div className="flex shrink-0 items-center py-[2px]">
          <Switch
            checked={trigger.enabled}
            disabled={busy}
            onCheckedChange={onToggle}
          />
        </div>
      </div>
      <div className="flex w-full items-start gap-[2px] px-[2px] pb-[2px]">
        <div
          className={`flex min-w-0 flex-1 flex-col px-[2px] ${fire ? "pt-1" : "py-1"}`}
        >
          <p className="px-[2px] text-[12px] leading-4 text-(--text-03)">
            <span className="font-bold">IF</span> {trigger.nl_description}
          </p>
          <p className="px-[2px] text-[12px] leading-4 text-(--text-03)">
            Then send to <span className="font-bold">{destNames}</span>
          </p>
          {message && (
            <p className="line-clamp-1 px-[2px] text-[12px] leading-4 font-semibold text-(--text-04)">
              {message}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-col items-end justify-between self-stretch">
          <Button
            icon={SvgSettings}
            size="sm"
            prominence="tertiary"
            tooltip="Edit watcher"
            onClick={onEdit}
          />
          {!fire && (
            <span className="flex items-center gap-[2px] p-[2px]">
              <span className="px-[2px] text-[12px] leading-4 whitespace-nowrap text-(--text-03)">
                No runs yet
              </span>
              <span className="flex size-5 items-center justify-center">
                <span className="size-2 rounded-full border border-(--border-02)" />
              </span>
            </span>
          )}
        </div>
      </div>
      {fire && (
        <Section
          justifyContent="start"
          alignItems="stretch"
          height="fit"
          gap={0}
          padding={0.25}
        >
          <div className="flex w-full items-start gap-[2px]">
            <div className="flex min-w-0 flex-1 items-center py-[2px]">
              <span className="shrink-0 px-[2px] text-[12px] leading-4 whitespace-nowrap text-(--text-03)">
                Sent message to
              </span>
              <span className="min-w-0 truncate px-[2px] text-[12px] leading-4 font-semibold whitespace-nowrap text-(--text-03)">
                {fireDest}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-[2px]">
              <span className="px-[2px] text-[12px] leading-4 whitespace-nowrap text-(--text-03)">
                {relativeTime(fire.ts, "long")}
              </span>
              <Button
                icon={detailsOpen ? SvgChevronUp : SvgChevronDown}
                size="xs"
                prominence="tertiary"
                tooltip="Details"
                onClick={onToggleDetails}
              />
            </div>
          </div>
          {detailsOpen && (
            <div className="pr-5">
              <p className="px-[2px] text-[12px] leading-4 font-semibold text-(--text-04)">
                {fire.message}
              </p>
            </div>
          )}
        </Section>
      )}
    </Section>
  );
}
