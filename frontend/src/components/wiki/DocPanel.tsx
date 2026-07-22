"use client";

import { useState, type ReactNode } from "react";
import { Tabs } from "@onyx-ai/opal/components";

export type DocPanelTab = "updates" | "comments" | "sources" | "watching";

const TABS: Array<{ value: DocPanelTab; label: string }> = [
  { value: "updates", label: "Updates" },
  { value: "comments", label: "Comments" },
  { value: "sources", label: "Sources" },
  { value: "watching", label: "Watching" },
];

const tabIndex = (tab: DocPanelTab) => TABS.findIndex((t) => t.value === tab);

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
  // The incoming surface slides from the side the underline travels toward.
  // Adjust-during-render binds the direction to the committed tree, safe
  // under StrictMode double renders and abandoned concurrent renders.
  const [lastTab, setLastTab] = useState(tab);
  const [fromLeft, setFromLeft] = useState(false);
  if (lastTab !== tab) {
    setFromLeft(tabIndex(tab) < tabIndex(lastTab));
    setLastTab(tab);
  }
  return (
    <div className="flex h-full w-[360px] max-w-[100vw] flex-col">
      {/* Strip metrics from the mock's panel Header (1790:52552): 12px
          sides, 8px top, 4px below the strip. The region draws no left
          border, the cards inside each tab carry their own. doc-panel-tabs
          scopes the strip's 14px trigger override (globals.css). */}
      <div className="doc-panel-tabs shrink-0 px-3 pt-2 pb-1">
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
      {/* Keyed by tab so the enter animation re-runs per switch (bodies
          already remount per tab, and state lives above the panel). The
          clip keeps the offset start inside the rail. */}
      <div
        key={tab}
        className={`flex min-h-0 flex-1 flex-col overflow-x-clip ${
          fromLeft ? "panel-tab-in-left" : "panel-tab-in-right"
        }`}
      >
        {children}
      </div>
    </div>
  );
}
