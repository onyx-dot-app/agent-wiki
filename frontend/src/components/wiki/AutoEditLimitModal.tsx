"use client";

import { useEffect, useState } from "react";
import { Button, InputTypeIn, Tag, Text } from "@onyx-ai/opal/components";
import { InputHorizontal } from "@onyx-ai/opal/layouts";
import { SvgBell, SvgPauseCircle, SvgSliders, SvgX } from "@onyx-ai/opal/icons";

interface UsageBarProps {
  count: number;
  /** Warning threshold in updates/24h. 0 alerts on every update, so no alert marker is drawn. */
  threshold: number;
  /** Admin cap in updates/24h, 0 = none set. */
  cap: number;
}

/** Auto-edit usage meter (mock 1790:52513): blue fill against the admin cap
 *  with tick marks and chips for the alert threshold and the cap. Renders
 *  nothing without a cap, per the mock's annotation. */
export function UsageBar({ count, threshold, cap }: UsageBarProps) {
  if (cap <= 0) return null;
  const pct = Math.min(count / cap, 1) * 100;
  const alertPct = threshold > 0 ? Math.min(threshold / cap, 1) * 100 : null;
  const warning = threshold > 0 && count >= threshold;
  const limit = count >= cap;
  return (
    <div className="flex w-full flex-col gap-1">
      <div className="relative h-1 w-full overflow-hidden rounded-(--radius-04) bg-(--background-tint-02)">
        <div
          className="absolute inset-y-0 left-0 rounded-(--radius-04) bg-(--theme-blue-05)"
          style={{ width: `${pct}%` }}
        />
        {alertPct !== null && (
          <div
            className="absolute inset-y-0 w-0.5 bg-(--theme-yellow-05)"
            style={{ left: `${alertPct}%` }}
          />
        )}
        <div className="absolute inset-y-0 right-0 w-0.5 bg-(--theme-orange-05)" />
      </div>
      <div className="flex items-center justify-end gap-1">
        {threshold > 0 && (
          <Tag
            title="Alert:"
            value={String(threshold)}
            color={warning ? "amber" : "gray"}
            icon={warning ? SvgBell : undefined}
          />
        )}
        <Tag
          title="Auto-Edit Limit:"
          value={String(cap)}
          color={limit ? "red" : "gray"}
          icon={limit ? SvgPauseCircle : undefined}
        />
      </div>
    </div>
  );
}

interface AutoEditLimitModalProps {
  onClose: () => void;
  count: number;
  threshold: number;
  cap: number;
  /** Explicit per-page threshold, null when using the workspace default. */
  ownThreshold: number | null;
  saving: boolean;
  /** Persists the per-page threshold, null clears back to the default.
   *  Resolves false when the save failed so the modal can stay open. */
  onSave: (value: number | null) => Promise<boolean>;
}

/** Auto-Edit Limit modal (mock 1899:349193): usage summary, the editable
 *  per-page alert threshold, and the admin-locked daily cap. Mount only
 *  while open so the draft re-seeds from the current policy each time. */
export function AutoEditLimitModal({
  onClose,
  count,
  threshold,
  cap,
  ownThreshold,
  saving,
  onSave,
}: AutoEditLimitModalProps) {
  const [draft, setDraft] = useState(
    ownThreshold != null ? String(ownThreshold) : "",
  );
  const parsed =
    draft.trim() === "" ? null : Math.floor(Math.max(0, Number(draft)));
  const valid = parsed === null || Number.isFinite(parsed);
  const dirty = parsed !== ownThreshold;

  // A failed save keeps the modal (and the draft) open so the edit isn't
  // lost. The panel's error text sits behind the scrim, visible once the
  // modal closes.
  const submit = async () => {
    if (!valid || !dirty) return;
    if (await onSave(parsed)) onClose();
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div
        onClick={onClose}
        aria-hidden
        className="fixed inset-0 z-[90] bg-(--mask-03)"
      />
      <div
        role="dialog"
        aria-label="Auto-Edit Limit"
        className="fixed top-1/2 left-1/2 z-[95] flex w-[min(480px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 flex-col gap-4 rounded-(--radius-12) bg-(--background-tint-00) p-4 shadow-(--shadow-modal)"
      >
        <div className="flex items-start gap-2">
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <SvgSliders size={20} className="text-(--text-04)" />
            <Text font="main-ui-action" color="text-05">
              Auto-Edit Limit
            </Text>
            <Text font="secondary-body" color="text-03">
              Set daily auto-edit limits to control LLM usage.
            </Text>
          </div>
          <Button
            icon={SvgX}
            prominence="tertiary"
            size="sm"
            tooltip="Close"
            onClick={onClose}
          />
        </div>

        <div className="flex flex-col gap-2 border-y border-(--border-01) py-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex flex-col">
              <Text font="main-ui-action" color="text-04">
                Past Day Updates
              </Text>
              <Text font="secondary-body" color="text-03">
                Last 24 hours
              </Text>
            </div>
            <div className="flex flex-col items-end">
              <Text font="main-ui-action" color="text-04">
                {String(count)}
              </Text>
              <Text font="secondary-body" color="text-03">
                Auto-Edits
              </Text>
            </div>
          </div>
          <UsageBar count={count} threshold={threshold} cap={cap} />
        </div>

        <InputHorizontal
          title="Alert Threshold"
          description="Notify the page owner when daily auto-edits reach this threshold."
        >
          <div className="w-40">
            <InputTypeIn
              type="number"
              min={0}
              step={1}
              value={draft}
              placeholder="Workspace default"
              clearButton
              onChange={(e) => setDraft(e.target.value)}
            />
          </div>
        </InputHorizontal>

        <InputHorizontal
          title="Daily Auto-Edit Limit"
          description="Pause auto-edits when daily auto-edits reach this limit."
        >
          <div className="flex w-40 flex-col gap-1">
            <InputTypeIn
              variant="disabled"
              value={cap > 0 ? String(cap) : "No limit"}
              onChange={() => undefined}
            />
            <Text font="secondary-body" color="text-03">
              Daily limit is set and locked by admins.
            </Text>
          </div>
        </InputHorizontal>

        <div className="flex justify-end gap-2">
          <Button prominence="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button
            variant="action"
            onClick={() => void submit()}
            disabled={saving || !valid || !dirty}
          >
            {saving ? "Saving…" : "Save Changes"}
          </Button>
        </div>
      </div>
    </>
  );
}
