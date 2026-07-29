"use client";

/** The Auto policy surfaces: the composite Auto mark, the anchored
 * floating-panel positioner, and the hover policy popover (mock
 * 1929:362227). */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Button, Divider, Switch } from "@onyx-ai/opal/components";
import { ContentAction, InputHorizontal, Section } from "@onyx-ai/opal/layouts";
import {
  SvgAddLines,
  SvgExpand,
  SvgOnyxOctagon,
  SvgSparkle,
  SvgTextLinesSmall,
} from "@onyx-ai/opal/icons";

import { toast } from "@/hooks/useToast";
import { pathKind } from "@/lib/wiki/utils";
import { type UpdatePolicyPatch } from "@/lib/updatePolicy";
import { saveUpdatePolicy, useUpdatePolicy } from "@/lib/wiki/hooks";

interface AutoGlyphProps {
  size?: number;
}

/** The Auto mark (mock 2079:379954): the octagon outline holding the blue
 * lines glyph, composed from Opal icons since no single asset ships it. */
export function AutoGlyph({ size = 16 }: AutoGlyphProps) {
  return (
    <Section
      gap={0}
      width="fit"
      height="fit"
      className="relative text-(--text-05)"
    >
      <SvgOnyxOctagon size={size} />
      <Section
        gap={0}
        width="full"
        height="full"
        alignItems="center"
        justifyContent="center"
        className="absolute inset-0 text-(--theme-blue-05)"
      >
        <SvgTextLinesSmall size={Math.round(size * 0.55)} />
      </Section>
    </Section>
  );
}

interface PanelSurfaceProps {
  children: ReactNode;
}

/** The shared floating-panel chrome (mock 1929:362227 "Policy Panel"). */
export function PanelSurface({ children }: PanelSurfaceProps) {
  return (
    <Section
      gap={0}
      justifyContent="start"
      alignItems="stretch"
      height="fit"
      padding={0.25}
      className="rounded-(--radius-12) border border-(--border-01) bg-(--background-tint-01) shadow-[0px_2px_12px_0px_var(--shadow-02),0px_0px_4px_1px_var(--shadow-01)]"
    >
      {children}
    </Section>
  );
}

interface AnchoredPanelProps {
  anchor: HTMLElement;
  onDismiss: () => void;
  /** Hover panels stay open while the pointer is inside them. */
  hover?: { onEnter: () => void; onLeave: () => void };
  /** Pass false when children carry their own surfaces (stacked panels,
   * self-chromed cards, mock 2283:84706). */
  chrome?: boolean;
  children: ReactNode;
}

/** Anchors a floating panel to a header cluster: fixed-position, right
 * edges aligned, just below the anchor. */
export function AnchoredPanel({
  anchor,
  onDismiss,
  hover,
  chrome = true,
  children,
}: AnchoredPanelProps) {
  const [rect, setRect] = useState(() => anchor.getBoundingClientRect());
  useEffect(() => {
    setRect(anchor.getBoundingClientRect());
  }, [anchor]);
  const panelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (hover) return;
    const onDown = (e: PointerEvent) => {
      const el = panelRef.current;
      if (
        el &&
        !el.contains(e.target as Node) &&
        !anchor.contains(e.target as Node)
      )
        onDismiss();
    };
    window.addEventListener("pointerdown", onDown, true);
    return () => window.removeEventListener("pointerdown", onDown, true);
  }, [anchor, hover, onDismiss]);
  return createPortal(
    // raw-ok: Popover.Anchor exists but Popover.Content bakes neutral-00/rounded-12/shadow-md chrome (WithoutStyles, no escape) that the mock's tint-01 policy panel and chromeless floating cards contradict, and this wrapper also needs runtime viewport coordinates
    <div
      ref={panelRef}
      className="fixed z-50 w-(--block-width-panel-medium-small)"
      style={{ top: rect.bottom + 8, right: window.innerWidth - rect.right }}
      onPointerEnter={hover?.onEnter}
      onPointerLeave={hover?.onLeave}
    >
      {chrome ? <PanelSurface>{children}</PanelSurface> : children}
    </div>,
    document.body,
  );
}

export interface OpenUpdatesPanelOpts {
  /** Open the side panel with the Page Instructions editor expanded. */
  editInstructions?: boolean;
}

interface PolicyPopoverProps {
  path: string;
  /** The policy PATCH is write-gated, so read-only viewers get a
   * disabled switch instead of a doomed request. */
  canWrite: boolean;
  onOpenUpdatesPanel?: (opts?: OpenUpdatesPanelOpts) => void;
}

/** The Auto popover (mock 1929:362227 "Policy Panel"): a read-write shortcut
 * into the scope's update policy, editing the same cache the side panel
 * reads. */
export function PolicyPopover({
  path,
  canWrite,
  onOpenUpdatesPanel,
}: PolicyPopoverProps) {
  const kind = pathKind(path);
  const { policy } = useUpdatePolicy(path);
  const effective = policy?.effective ?? null;
  const [saving, setSaving] = useState(false);

  const allowed = !!effective?.ai_management_allowed;
  const autoUpdateDisabled = !!effective?.ingestion_auto_update_disabled;
  const toggle = (patch: UpdatePolicyPatch) => {
    if (!policy) return;
    setSaving(true);
    saveUpdatePolicy(path, patch, policy)
      .catch((e) =>
        toast.error(
          e instanceof Error ? e.message : "Couldn't update the policy",
        ),
      )
      .finally(() => setSaving(false));
  };

  return (
    <Section
      justifyContent="start"
      alignItems="stretch"
      height="fit"
      gap={0.25}
      className="w-full"
    >
      <Section gap={0} height="fit" alignItems="stretch" padding={0.5}>
        {/* Group header — the switches live on the two rows below:
            Update = ingestion auto-update, Organize = auto management. */}
        <InputHorizontal
          icon={SvgSparkle}
          title="AI Auto-Edits"
          description={`Let AI update/organize this ${kind} on its own.`}
        />
        {/* raw-ok: Section drops pl-* for its own inline padding, and ml-*
            would shift the row and push the right-aligned switches past the
            popover edge. */}
        <div className="mt-2 flex flex-col gap-2 pl-6">
          <InputHorizontal
            title="Update"
            description="Periodically scan ingested data sources to add relevant new information."
          >
            <Switch
              checked={!autoUpdateDisabled}
              // Held until this path's policy loads (toggling against the
              // null default would persist a wrong override) and while a
              // save is in flight (a second click would race the PATCH).
              disabled={!canWrite || !effective || saving}
              onCheckedChange={(on) =>
                toggle({ ingestion_auto_update_disabled: !on })
              }
            />
          </InputHorizontal>
          <InputHorizontal
            title="Organize"
            description={`Reorganize, move, and/or merge content in this ${kind} when needed.`}
          >
            <Switch
              checked={allowed}
              disabled={!canWrite || !effective || saving}
              onCheckedChange={(on) => toggle({ ai_management_allowed: on })}
            />
          </InputHorizontal>
        </div>
      </Section>
      <Divider />
      <Section gap={0} height="fit" alignItems="stretch" padding={0.5}>
        {/* ContentAction rather than InputHorizontal for the
            descriptionMaxLines clamp (mock annotation: real value, 3 lines). */}
        <ContentAction
          icon={SvgAddLines}
          title="Page Instructions"
          description={
            effective?.update_instruction ||
            `How should this ${kind} be updated?`
          }
          descriptionMaxLines={3}
          sizePreset="main-ui"
          variant="section"
          width="full"
          padding="fit"
          rightChildren={
            <Button
              icon={SvgExpand}
              size="md"
              prominence="tertiary"
              tooltip="Edit in panel"
              onClick={() => onOpenUpdatesPanel?.({ editInstructions: true })}
            />
          }
        />
      </Section>
    </Section>
  );
}
