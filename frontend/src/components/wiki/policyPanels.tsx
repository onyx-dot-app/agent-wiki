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
import { OrganizeComingSoonRow } from "@/components/wiki/UpdatePolicyPanel";
import { pathKind } from "@/lib/wiki/utils";
import {
  getUpdatePolicy,
  patchUpdatePolicy,
  type EffectivePolicy,
} from "@/lib/updatePolicy";

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

interface PolicyPopoverProps {
  path: string;
  /** The policy PATCH is write-gated, so read-only viewers get a
   * disabled switch instead of a doomed request. */
  canWrite: boolean;
  onOpenUpdatesPanel?: () => void;
}

/** The Auto popover (mock 1929:362227 "Policy Panel"): the AI auto-edit
 * toggles and the scope's update instruction, all live on the update
 * policy the full panel edits. */
export function PolicyPopover({
  path,
  canWrite,
  onOpenUpdatesPanel,
}: PolicyPopoverProps) {
  const kind = pathKind(path);
  // The policy carries the path it was fetched for: a mismatch reads as
  // unloaded in the same render a navigation lands, so a stale page's
  // value can never be shown or PATCHed against the new path.
  const [policy, setPolicy] = useState<{
    forPath: string;
    effective: EffectivePolicy;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let alive = true;
    getUpdatePolicy(path)
      .then(
        (p) => alive && setPolicy({ forPath: path, effective: p.effective }),
      )
      .catch(() => alive && setPolicy(null));
    return () => {
      alive = false;
    };
  }, [path]);
  const loaded = policy?.forPath === path ? policy.effective : null;

  const allowed = !!loaded?.ai_management_allowed;
  const autoUpdateDisabled = !!loaded?.ingestion_auto_update_disabled;
  const patchField = async (patch: Partial<EffectivePolicy>) => {
    if (!loaded) return;
    setSaving(true);
    setPolicy({ forPath: path, effective: { ...loaded, ...patch } });
    try {
      await patchUpdatePolicy(path, patch);
    } catch (e) {
      // The pre-patch snapshot is still in `loaded`; putting it back
      // rolls the optimistic write off.
      setPolicy({ forPath: path, effective: loaded });
      toast.error(
        e instanceof Error ? e.message : "Couldn't update the policy",
      );
    } finally {
      setSaving(false);
    }
  };

  // Mock 2079:379824 annotation: clicking the popover body (any field)
  // opens the full side panel; the switches keep their inline toggles by
  // stopping the bubble.
  return (
    <Section
      justifyContent="start"
      alignItems="stretch"
      height="fit"
      gap={0.25}
      className="w-full cursor-pointer"
      onClick={onOpenUpdatesPanel}
    >
      <Section gap={0} height="fit" alignItems="stretch" padding={0.5}>
        <InputHorizontal
          icon={SvgSparkle}
          title="AI Auto-Edits"
          description={`Let AI update/organize this ${kind} on its own.`}
        >
          <span onClick={(e) => e.stopPropagation()}>
            <Switch
              checked={allowed}
              // Held until this path's policy loads (toggling against the
              // null default would persist a wrong override) and while a
              // save is in flight (a second click would race the PATCH).
              disabled={!canWrite || !loaded || saving}
              onCheckedChange={() =>
                void patchField({ ai_management_allowed: !allowed })
              }
            />
          </span>
        </InputHorizontal>
        {allowed && (
          <Section
            justifyContent="start"
            alignItems="stretch"
            height="fit"
            gap={0.5}
            className="mt-2 ml-6"
          >
            <InputHorizontal
              title="Update"
              description="Periodically scan ingested data sources to add relevant new information."
            >
              <span onClick={(e) => e.stopPropagation()}>
                <Switch
                  checked={!autoUpdateDisabled}
                  disabled={!canWrite || !loaded || saving}
                  onCheckedChange={() =>
                    void patchField({
                      ingestion_auto_update_disabled: !autoUpdateDisabled,
                    })
                  }
                />
              </span>
            </InputHorizontal>
            <OrganizeComingSoonRow kind={kind} />
          </Section>
        )}
      </Section>
      <Divider />
      <Section gap={0} height="fit" alignItems="stretch" padding={0.5}>
        {/* ContentAction rather than InputHorizontal for the
            descriptionMaxLines clamp (mock annotation: real value, 3 lines). */}
        <ContentAction
          icon={SvgAddLines}
          title="Page Instructions"
          description={
            loaded?.update_instruction || `How should this ${kind} be updated?`
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
              tooltip="Open in panel"
              // stopPropagation: the popover body opens the panel too, and
              // the bubble would double-fire the handler.
              onClick={(e) => {
                e.stopPropagation();
                onOpenUpdatesPanel?.();
              }}
            />
          }
        />
      </Section>
    </Section>
  );
}
