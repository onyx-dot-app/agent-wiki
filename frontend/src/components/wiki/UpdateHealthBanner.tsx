"use client";

import { Button } from "@onyx-ai/opal/components";
import { useEffect, useState } from "react";

import { fetchUpdateHealth, type UpdateHealth } from "@/lib/wiki";

interface Props {
  path: string;
  // Opens the Update Policy panel so the owner can adjust the threshold or
  // re-enable auto-update.
  onOpenPolicy: () => void;
}

/**
 * Owner-facing banner shown when a page is auto-updating too frequently (or had
 * its auto-update turned off by the org cap). Pull-based: reflects live
 * `update-health`, and renders nothing for non-owners or healthy pages. Mirrors
 * the inline warning-banner pattern used elsewhere in the wiki page view.
 */
export function UpdateHealthBanner({ path, onOpenPolicy }: Props) {
  const [health, setHealth] = useState<UpdateHealth | null>(null);

  useEffect(() => {
    let alive = true;
    setHealth(null);
    fetchUpdateHealth(path)
      .then((h) => {
        if (alive) setHealth(h);
      })
      .catch(() => {
        // Non-fatal — just don't show the banner.
      });
    return () => {
      alive = false;
    };
  }, [path]);

  if (!health?.show_banner) return null;

  const message = health.auto_disabled
    ? `Auto-update was turned off — this page exceeded the limit of ${health.cap} updates per day.`
    : `This page was auto-updated ${health.count} times in the past 24 hours` +
      (health.threshold > 0 ? ` (warns at ${health.threshold}).` : ".");

  return (
    <div className="mb-3 flex items-center gap-3 rounded-(--border-radius-08) border border-(--status-warning-02) bg-(--status-warning-01) px-3 py-2 text-[13px] text-(--status-text-warning-05)">
      <span aria-hidden>⚠</span>
      <span>{message}</span>
      <div className="flex-1" />
      <Button size="sm" onClick={onOpenPolicy}>
        Review settings
      </Button>
    </div>
  );
}
