"use client";

import { Button, MessageCard } from "@onyx-ai/opal/components";

import { useUpdateHealth } from "@/lib/wiki";

interface Props {
  path: string;
  // Opens the Update Policy panel so the owner can adjust the threshold or
  // re-enable auto-update.
  onOpenPolicy: () => void;
}

/**
 * Banner shown when a page is auto-updating too frequently. Pull-based: polls
 * the raw `update-health` facts (via the shared SWR hook, so it appears and
 * clears without a manual reload) and decides here whether to show — count
 * at/over the page's warning threshold. Mirrors the inline warning-banner
 * pattern used elsewhere in the wiki page view.
 */
export function UpdateHealthBanner({ path, onOpenPolicy }: Props) {
  const { health } = useUpdateHealth(path);

  // Client-side decision from the raw facts: warn when the page has had updates
  // at/over its threshold (threshold 0 = every update). Suppressed when
  // auto-update is off (nothing to warn about), and shown only to viewers who
  // can manage the page — the warning + its "Review settings" CTA are only
  // actionable for writers, and the threshold isn't a non-owner's concern.
  const shouldWarn =
    !!health &&
    health.can_manage &&
    !health.auto_update_disabled &&
    health.count_24h > 0 &&
    health.count_24h >= health.threshold_24h;
  if (!shouldWarn) return null;

  // Advisory frequency warning. The owner can act on it (raise the threshold
  // or turn off auto-update), so it keeps the "Review settings" CTA.
  const description =
    `Auto-updated ${health.count_24h} times in the past 24 hours` +
    (health.threshold_24h > 0
      ? `, above the warning threshold of ${health.threshold_24h}.`
      : ".");

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
      />
    </div>
  );
}
