"use client";

import { useState } from "react";

import { Button, Switch, Text } from "@onyx-ai/opal/components";
import { markdown } from "@onyx-ai/opal/utils";
import {
  SvgChevronDown,
  SvgChevronUp,
  SvgInfo,
  SvgSettings,
  SvgWorkflow,
} from "@onyx-ai/opal/icons";

import { AvatarCluster, ScopeChip } from "@/components/triggers/fireParts";
import type { DestinationConfig, Trigger, TriggerFire } from "@/lib/triggers";

/** The card's per-fire "Sent message to X · N ago" line names the config the
 * fire was recorded against; a deleted config degrades to the type name. */
function fireDestinationName(
  fire: TriggerFire,
  configs: DestinationConfig[],
): string {
  if (fire.destination_type === "event_log" || !fire.destination_config_id)
    return "Activity Center";
  const cfg = configs.find((c) => c.id === fire.destination_config_id);
  return cfg?.name ?? fire.destination_type;
}

interface Props {
  trigger: Trigger;
  fires: TriggerFire[];
  configs: DestinationConfig[];
  ownerName: string;
  busy?: boolean;
  formatRelative: (iso: string | null | undefined) => string;
  onToggle: () => void;
  onEdit: () => void;
  onHistory: () => void;
}

/** One trigger on the Triggers page: scope chip + owner header with the
 * enable switch, then either the recent-fire lines (expandable messages)
 * or the IF/Then summary when the trigger has never fired. */
export function TriggerCard({
  trigger: t,
  fires,
  configs,
  ownerName,
  busy,
  formatRelative,
  onToggle,
  onEdit,
  onHistory,
}: Props) {
  // The newest fire renders expanded on enabled triggers (per the mock);
  // clicks override per row. Derived, since fires arrive after mount.
  const [overrides, setOverrides] = useState<Map<number, boolean>>(new Map());
  const isRowOpen = (f: TriggerFire) =>
    overrides.get(f.event_id) ?? (t.enabled && f === fires[0]);

  const destinationTypes = [
    ...new Set(
      t.actions.map((a) => {
        if (!a.destination_config_id) return "event_log";
        const cfg = configs.find((c) => c.id === a.destination_config_id);
        return cfg?.type ?? "event_log";
      }),
    ),
  ];

  function toggleRow(f: TriggerFire) {
    setOverrides((cur) => new Map(cur).set(f.event_id, !isRowOpen(f)));
  }

  return (
    <div
      className={`box-border w-full rounded-(--radius-16) border border-(--border-01) p-3 ${
        t.enabled ? "bg-(--background-tint-00)" : "bg-(--background-tint-01)"
      } ${busy ? "pointer-events-none opacity-60" : ""}`}
    >
      <div className="flex w-full items-center p-[2px]">
        <div className="flex min-w-0 flex-1 items-center gap-1 p-[2px]">
          <ScopeChip scope={t.scope_path} />
          <span className="flex size-4 items-center justify-center p-[2px]">
            <SvgWorkflow className="size-3 text-(--text-03)" />
          </span>
          <AvatarCluster
            ownerName={ownerName}
            destinationTypes={destinationTypes}
          />
          <span className="min-w-0 px-[2px]">
            <Text font="secondary-body" color="text-03" nowrap maxLines={1}>
              {`${ownerName} (you)`}
            </Text>
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            type="button"
            icon={SvgInfo}
            size="sm"
            prominence="tertiary"
            tooltip="Details and history"
            onClick={onHistory}
            disabled={busy}
          />
          <Button
            type="button"
            icon={SvgSettings}
            size="sm"
            prominence="tertiary"
            tooltip="Edit trigger"
            onClick={onEdit}
            disabled={busy}
          />
          <Switch
            checked={t.enabled}
            onCheckedChange={onToggle}
            disabled={busy}
          />
        </div>
      </div>

      <div className="flex w-full flex-col p-1">
        {fires.length === 0 ? (
          <div className="flex w-full items-start gap-[2px]">
            <div className="flex min-w-0 flex-1 flex-col py-[2px]">
              <span className="px-[2px]">
                <Text font="secondary-body" color="text-03">
                  {markdown(`**IF** ${t.nl_description}`)}
                </Text>
              </span>
              {t.actions.map((a, i) => {
                const cfg = a.destination_config_id
                  ? configs.find((c) => c.id === a.destination_config_id)
                  : null;
                const dest = a.destination_config_id
                  ? (cfg?.name ?? "(destination removed)")
                  : "Activity Center";
                return (
                  <span
                    key={`${a.destination_config_id ?? "event_log"}-${i}`}
                    className="px-[2px]"
                  >
                    <Text font="secondary-body" color="text-03">
                      {markdown(`Then send message to **${dest}**`)}
                    </Text>
                  </span>
                );
              })}
              {t.actions[0]?.message && (
                <span className="px-[2px]">
                  <Text font="secondary-action" color="text-03">
                    {t.actions[0].message}
                  </Text>
                </span>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-[2px]">
              <span className="px-[2px]">
                <Text font="secondary-body" color="text-03" nowrap>
                  No runs yet
                </Text>
              </span>
              <span className="flex size-5 items-center justify-center">
                <span className="size-2 rounded-full border border-(--border-02)" />
              </span>
            </div>
          </div>
        ) : (
          fires.map((f) => {
            const isOpen = isRowOpen(f);
            const Chevron = isOpen ? SvgChevronUp : SvgChevronDown;
            return (
              <div key={f.event_id} className="flex w-full flex-col">
                <div className="flex w-full items-center gap-[2px]">
                  <div className="flex min-w-0 flex-1 items-center py-[2px]">
                    <span className="shrink-0 px-[2px]">
                      <Text font="secondary-body" color="text-03" nowrap>
                        Sent message to
                      </Text>
                    </span>
                    <span className="min-w-0 px-[2px]">
                      <Text
                        font="secondary-action"
                        color="text-03"
                        nowrap
                        maxLines={1}
                      >
                        {fireDestinationName(f, configs)}
                      </Text>
                    </span>
                  </div>
                  <div className="flex shrink-0 items-center gap-[2px]">
                    <span className="px-[2px]">
                      <Text font="secondary-body" color="text-03" nowrap>
                        {formatRelative(f.ts)}
                      </Text>
                    </span>
                    {/* raw-ok: 20px inline chevron; Opal Button's smallest container oversizes this row */}
                    <button
                      type="button"
                      onClick={() => toggleRow(f)}
                      aria-expanded={isOpen}
                      title="Details"
                      className="flex size-5 cursor-pointer items-center justify-center rounded-(--radius-08) border-0 bg-transparent p-[2px] hover:bg-(--background-tint-02)"
                    >
                      <Chevron className="size-3.5 text-(--text-03)" />
                    </button>
                  </div>
                </div>
                {isOpen && f.message && (
                  <div className="w-full pr-5 pb-1 [&>span]:block">
                    <Text font="main-ui-body" color="text-03">
                      {markdown(f.message)}
                    </Text>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
