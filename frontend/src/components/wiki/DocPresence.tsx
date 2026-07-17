"use client";

import { Button, IconContainer } from "@onyx-ai/opal/components";
import { SvgSparkle } from "@onyx-ai/opal/icons";
import type { CoeditParticipant } from "@/lib/coeditor/types";
import { useUpdateHealth, type UpdateHealth } from "@/lib/wiki";

// Ring palette cycles per participant (mock 1807:54455). All Opal theme
// tokens, order matches the mock's blue/mint/yellow/coral/magenta/violet run.
const RING_TOKENS = [
  "--theme-blue-05",
  "--theme-green-05",
  "--theme-yellow-05",
  "--theme-orange-05",
  "--theme-magenta-05",
  "--theme-purple-05",
];
const MAX_AVATARS = 7;

type AutoEditState = "on" | "warning" | "limit" | "off";

function autoEditState(health: UpdateHealth | null | undefined): AutoEditState {
  if (!health) return "on";
  if (health.auto_update_disabled) return "off";
  if (health.cap_24h > 0 && health.count_24h >= health.cap_24h) return "limit";
  if (health.count_24h > 0 && health.count_24h >= health.threshold_24h)
    return "warning";
  return "on";
}

const STATE_RING: Record<AutoEditState, string> = {
  on: "--theme-blue-05",
  warning: "--theme-yellow-05",
  limit: "--theme-orange-05",
  off: "--border-02",
};

const STATE_TOOLTIP: Record<AutoEditState, string> = {
  on: "AI auto-edits on",
  warning: "Approaching the daily auto-edit limit",
  limit: "Auto-edit limit reached, updates paused",
  off: "AI auto-edits off",
};

interface DocPresenceProps {
  path: string;
  participants: CoeditParticipant[];
  onOpenUpdates: () => void;
}

/** Header presence cluster (mock 1807:54453): overlapping participant
 *  avatars, then the agent status avatar whose ring color tracks the page's
 *  auto-edit health. Clicking the agent avatar opens the Updates tab. With
 *  no participants only the status avatar renders. */
export function DocPresence({
  path,
  participants,
  onOpenUpdates,
}: DocPresenceProps) {
  const { health } = useUpdateHealth(path);
  const state = autoEditState(health);

  const visible = participants.slice(0, MAX_AVATARS);
  const overflow = participants.length - visible.length;

  return (
    <div className="flex items-center gap-1">
      {visible.length > 0 && (
        <div className="flex items-center">
          {visible.map((p, i) => (
            <span
              key={p.user_id}
              title={p.user_display}
              className="relative -ml-1 rounded-(--radius-round) border bg-(--background-tint-00) first:ml-0"
              style={{
                borderColor: `var(${RING_TOKENS[i % RING_TOKENS.length]})`,
                zIndex: visible.length - i,
              }}
            >
              <IconContainer avatar="user" name={p.user_display} />
            </span>
          ))}
          {overflow > 0 && (
            <span className="relative z-0 -ml-1 flex size-5 items-center justify-center rounded-(--radius-round) bg-(--background-tint-02) text-xs font-semibold text-(--text-03)">
              +{overflow}
            </span>
          )}
        </div>
      )}
      {visible.length > 0 && <div className="mx-1 h-4 w-px bg-(--border-01)" />}
      <Button
        prominence="internal"
        size="sm"
        tooltip={STATE_TOOLTIP[state]}
        onClick={onOpenUpdates}
        icon={({ style }) => (
          <span
            style={{ ...style, borderColor: `var(${STATE_RING[state]})` }}
            className={`flex items-center justify-center rounded-(--radius-round) border bg-(--background-tint-00) ${
              state === "off" ? "text-(--text-02)" : "text-(--text-04)"
            }`}
          >
            <IconContainer icon={SvgSparkle} size="secondary" />
          </span>
        )}
      />
    </div>
  );
}
