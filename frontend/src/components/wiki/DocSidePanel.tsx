"use client";

import type { ReactNode } from "react";
import { EmptyMessageCard, Tabs } from "@onyx-ai/opal/components";
import { SvgBookOpen, SvgEye } from "@onyx-ai/opal/icons";

export type DocPanelTab = "updates" | "comments" | "sources" | "watching";

interface DocSidePanelProps {
  tab: DocPanelTab;
  onTabChange: (tab: DocPanelTab) => void;
  updates: ReactNode;
  comments: ReactNode;
}

/** Tabbed right-rail host for a document's side surfaces. One panel, one
 *  tab strip, content switches in place. Sources and Watching render
 *  placeholders until their backends exist. */
export function DocSidePanel({
  tab,
  onTabChange,
  updates,
  comments,
}: DocSidePanelProps) {
  return (
    <div className="flex h-full w-full flex-col">
      <div className="flex h-12 shrink-0 items-center px-3">
        <Tabs
          variant="underline"
          value={tab}
          onValueChange={(v) => onTabChange(v as DocPanelTab)}
        >
          <Tabs.List>
            <Tabs.Trigger value="updates">Updates</Tabs.Trigger>
            <Tabs.Trigger value="comments">Comments</Tabs.Trigger>
            <Tabs.Trigger value="sources">Sources</Tabs.Trigger>
            <Tabs.Trigger value="watching">Watching</Tabs.Trigger>
          </Tabs.List>
        </Tabs>
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-2 pb-2">
        {tab === "updates" && updates}
        {tab === "comments" && comments}
        {tab === "sources" && (
          <EmptyMessageCard
            sizePreset="main-ui"
            icon={SvgBookOpen}
            title="Sources"
            description="Coming soon. Citations from ingested data sources will show up here."
          />
        )}
        {tab === "watching" && (
          <EmptyMessageCard
            sizePreset="main-ui"
            icon={SvgEye}
            title="Watching"
            description="Coming soon. Changes to pages you watch will show up here."
          />
        )}
      </div>
    </div>
  );
}
