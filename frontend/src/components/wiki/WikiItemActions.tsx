"use client";

import { type ReactNode } from "react";
import { Divider, LineItemButton, Popover } from "@onyx-ai/opal/components";
import {
  SvgEdit,
  SvgFolderIn,
  SvgFolderPlus,
  SvgLink,
  SvgPlus,
  SvgShare,
  SvgSparkle,
  SvgTrash,
} from "@onyx-ai/opal/icons";
import { useRowActions } from "@/providers/WikiItemActionsProvider";

export interface WikiItemMenuProps {
  path: string;
  isFolder: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  align?: "start" | "center" | "end";
  /** Replace the provider's rename/delete with surface-local flows (e.g. the
   * folder explorer's inline rename and styled confirm). */
  overrides?: { rename?: () => void; remove?: () => void };
  children: ReactNode;
}

/**
 * Per-item "⋯" actions menu. Spec: 160px wide, compact line items, dividers,
 * Delete in danger red. Folders lead with New Page / New Folder and omit
 * "Launch Agent". The caller supplies the trigger as `children`.
 */
export default function WikiItemMenu({
  path,
  isFolder,
  open,
  onOpenChange,
  align = "start",
  overrides,
  children,
}: WikiItemMenuProps) {
  const actions = useRowActions();
  const run = (fn: (p: string) => void) => () => {
    onOpenChange(false);
    fn(path);
  };
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <Popover.Trigger asChild>{children}</Popover.Trigger>
      <Popover.Content align={align} sideOffset={4} width="fit">
        <div className="box-border flex w-[160px] flex-col gap-[2px]">
          {isFolder && (
            <>
              <LineItemButton
                variant="body"
                sizePreset="main-ui"
                icon={SvgPlus}
                title="New Page"
                onClick={run(actions.newPage)}
              />
              <LineItemButton
                variant="body"
                sizePreset="main-ui"
                icon={SvgFolderPlus}
                title="New Folder"
                onClick={run(actions.newFolder)}
              />
              <Divider />
            </>
          )}
          <LineItemButton
            variant="body"
            sizePreset="main-ui"
            icon={SvgShare}
            title="Share"
            onClick={run(actions.share)}
          />
          <LineItemButton
            variant="body"
            sizePreset="main-ui"
            icon={SvgEdit}
            title="Rename"
            onClick={run(overrides?.rename ?? actions.rename)}
          />
          <LineItemButton
            variant="body"
            sizePreset="main-ui"
            icon={SvgFolderIn}
            title="Move"
            onClick={run(actions.move)}
          />
          <Divider />
          <LineItemButton
            variant="body"
            sizePreset="main-ui"
            icon={SvgLink}
            title="Copy Link"
            onClick={run(actions.copyLink)}
          />
          {!isFolder && (
            <LineItemButton
              variant="body"
              sizePreset="main-ui"
              icon={SvgSparkle}
              title="Launch Agent"
              onClick={run(actions.launchAgent)}
            />
          )}
          <Divider />
          <span className="!text-[color:var(--status-text-error-05)] [&_*]:!text-[color:var(--status-text-error-05)]">
            <LineItemButton
              variant="body"
              sizePreset="main-ui"
              icon={SvgTrash}
              title="Delete"
              onClick={() => {
                onOpenChange(false);
                if (overrides?.remove) overrides.remove();
                else actions.remove(path, isFolder);
              }}
            />
          </span>
        </div>
      </Popover.Content>
    </Popover>
  );
}
