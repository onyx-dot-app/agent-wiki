"use client";

import { Button, MessageCard } from "@onyx-ai/opal/components";

import { useUpdateHealth } from "@/lib/wiki";

interface Props {
  path: string;
  // Opens the Update Policy panel so the owner can adjust the threshold or
  // re-enable auto-update. Used by the frequency warning only.
  onOpenPolicy: () => void;
}

/**
 * Pull-based banner for a page's auto-update health. Polls the raw
 * `update-health` facts (via the shared SWR hook, so it appears and clears
 * without a manual reload) and decides client-side which state to show. Only
 * surfaced to viewers who can manage the page, and never when auto-update is
 * off — there's nothing to act on.
 */
export function UpdateHealthBanner({ path, onOpenPolicy }: Props) {
  const { health } = useUpdateHealth(path);

  if (!health || !health.can_manage || health.auto_update_disabled) return null;

  // Cap takes precedence over the warning: once a page hits the admin cap, the
  // ingest pipeline pauses its auto-updates until the trailing-24h count rolls
  // back under the cap. The cap is admin-controlled, so there's no owner action
  // and no "Review settings" CTA — this banner is purely informational.
  if (health.cap_24h > 0 && health.count_24h >= health.cap_24h) {
    const resumes = health.cap_resets_at
      ? new Date(health.cap_resets_at).toLocaleString(undefined, {
          dateStyle: "medium",
          timeStyle: "short",
        })
      : null;
    const description =
      `This page hit the workspace limit of ${health.cap_24h} auto-updates in 24 hours, ` +
      (resumes
        ? `so it won't auto-update until around ${resumes}. `
        : "so automatic updates are paused until the rate drops. ") +
      "The limit is set by an admin.";
    return (
      <div className="mb-3">
        <MessageCard
          variant="error"
          title="Auto-update paused — update limit reached"
          description={description}
        />
      </div>
    );
  }

  // Advisory frequency warning (threshold 0 = every update). The owner can act
  // on it, so it keeps the CTA + a short decision-tree of remedies: is the
  // churn expected (raise the threshold) or not (narrow what auto-update does,
  // or the page's scope)?
  if (health.count_24h > 0 && health.count_24h >= health.threshold_24h) {
    const description =
      `Auto-updated ${health.count_24h} times in the past 24 hours` +
      (health.threshold_24h > 0
        ? `, above the warning threshold of ${health.threshold_24h}. `
        : ". ") +
      "Check whether this is expected, then:";
    return (
      <div className="mb-3">
        <MessageCard
          variant="warning"
          title="This page is updating frequently"
          description={description}
          rightChildren={
            <Button size="sm" onClick={onOpenPolicy}>
              Review settings
            </Button>
          }
          bottomChildren={
            <ul className="m-0 flex list-disc flex-col gap-1 pl-4 text-[13px] text-(--text-03)">
              <li>
                If this rate is expected, raise the warning threshold so it
                stops flagging.
              </li>
              <li>
                If auto-update is changing the wrong things, add update
                instructions telling it what to keep or ignore.
              </li>
              <li>
                If the page simply covers too much, narrow its scope — tighten
                what it&apos;s about, or split it into focused pages.
              </li>
            </ul>
          }
        />
      </div>
    );
  }

  return null;
}
