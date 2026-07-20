"use client";

import { type ReactNode } from "react";
import { Tabs } from "@onyx-ai/opal/components";

export type DocPanelTab = "updates" | "comments" | "sources" | "watching";

const TABS: Array<{ value: DocPanelTab; label: string }> = [
  { value: "updates", label: "Updates" },
  { value: "comments", label: "Comments" },
  { value: "sources", label: "Sources" },
  { value: "watching", label: "Watching" },
];

interface DocPanelProps {
  tab: DocPanelTab;
  onTabChange: (tab: DocPanelTab) => void;
  /** Active tab's surface. The page renders it so cross-tab state (comment
   * threads, trigger status) lives above the panel and survives tab moves. */
  children: ReactNode;
}

/** The doc page's right-rail panel (mock 1790:52200): an Updates | Comments |
 * Sources | Watching tab strip over the active surface. The rail holds one
 * occupant at a time, so the tabbed surfaces render inside this panel
 * rather than as their own rail columns. */
export function DocPanel({ tab, onTabChange, children }: DocPanelProps) {
  return (
    <div className="flex h-full w-[360px] max-w-[100vw] flex-col border-l border-(--border-01)">
      <div className="shrink-0 px-2 pt-1">
        <Tabs
          variant="underline"
          value={tab}
          onValueChange={(v) => onTabChange(v as DocPanelTab)}
        >
          <Tabs.List>
            {TABS.map((t) => (
              <Tabs.Trigger key={t.value} value={t.value}>
                {t.label}
              </Tabs.Trigger>
            ))}
          </Tabs.List>
        </Tabs>
      </div>
      <div className="flex min-h-0 flex-1 flex-col">{children}</div>
    </div>
  );
}
