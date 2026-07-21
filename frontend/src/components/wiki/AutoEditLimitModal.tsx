"use client";

import { useEffect, useState, type ReactNode } from "react";
import {
  Button,
  Divider,
  InputTypeIn,
  Tag,
  Text,
} from "@onyx-ai/opal/components";
import { Content, InputHorizontal, Section } from "@onyx-ai/opal/layouts";
import {
  SvgBell,
  SvgChevronDown,
  SvgChevronUp,
  SvgPauseCircle,
  SvgSliders,
  SvgX,
} from "@onyx-ai/opal/icons";

import { apiFetch } from "@/lib/api";

interface UsageBarProps {
  count: number;
  /** Warning threshold in updates/24h. 0 alerts on every update, so no alert marker is drawn. */
  threshold: number;
  /** Admin cap in updates/24h, 0 = none set. */
  cap: number;
}

/** Auto-edit usage meter (mock 1899:349732): white track, blue fill that
 *  turns amber past the alert tick, a tinted warning zone behind the tick,
 *  and always-iconed annotation chips. Renders nothing without a cap, per
 *  the mock's annotation. */
export function UsageBar({ count, threshold, cap }: UsageBarProps) {
  if (cap <= 0) return null;
  const pct = Math.min(count / cap, 1) * 100;
  const alertPct = threshold > 0 ? Math.min(threshold / cap, 1) * 100 : null;
  const warning = threshold > 0 && count >= threshold;
  const limit = count >= cap;
  return (
    <Section alignItems="stretch" height="fit" gap={0}>
      <div className="flex w-full items-center py-[6px]">
        <div className="relative h-1 w-full overflow-clip rounded-(--radius-04) bg-(--background-tint-00)">
          {/* Warning zone: faint tick-yellow band past the alert marker,
              capped at 40px and clipped at the track's end. */}
          {alertPct !== null && (
            <div
              className="absolute inset-y-0 w-10 bg-[color-mix(in_srgb,var(--theme-yellow-05)_7.5%,transparent)]"
              style={{ left: `${alertPct}%` }}
            />
          )}
          {count > 0 && (
            <div
              className="absolute inset-y-0 left-0 min-w-1 rounded-r-(--radius-04) bg-(--blue-45)"
              style={{
                width: `${warning && alertPct !== null ? alertPct : pct}%`,
              }}
            />
          )}
          {/* Fill past the threshold flips to amber. */}
          {warning && alertPct !== null && pct > alertPct && (
            <div
              className="absolute inset-y-0 rounded-r-(--radius-04) bg-(--neon-amber)"
              style={{ left: `${alertPct}%`, width: `${pct - alertPct}%` }}
            />
          )}
          {alertPct !== null && (
            <div
              className="absolute inset-y-0 w-0.5 bg-(--theme-yellow-05)"
              style={{ left: `${alertPct}%` }}
            />
          )}
          <div className="absolute inset-y-0 right-0 w-0.5 bg-(--status-warning-05)" />
        </div>
      </div>
      <Section
        flexDirection="row"
        justifyContent="end"
        alignItems="start"
        height="fit"
        gap={0.25}
      >
        {threshold > 0 && (
          <Tag
            title="Alert:"
            value={String(threshold)}
            color={warning ? "amber" : "gray"}
            icon={SvgBell}
          />
        )}
        <Tag
          title="Auto-Edit Limit:"
          value={String(cap)}
          color={limit ? "red" : "gray"}
          icon={SvgPauseCircle}
        />
      </Section>
    </Section>
  );
}

/** Vertical spin control nested inside a numeric input (mock 2897:49790). */
function Stepper({
  value,
  onChange,
  max,
  disabled,
}: {
  value: string;
  onChange: (next: string) => void;
  max?: number;
  disabled?: boolean;
}) {
  const num = value.trim() === "" ? 0 : Number(value);
  const step = (d: number) => {
    const next = Math.max(0, num + d);
    onChange(String(max != null ? Math.min(next, max) : next));
  };
  return (
    <div className="flex w-6 flex-col items-stretch">
      {/* raw-ok: Opal has no number-stepper control. These are the input's nested 12px spin halves */}
      <button
        type="button"
        aria-label="Increase"
        disabled={disabled}
        onClick={() => step(1)}
        className="flex h-3 cursor-pointer items-center justify-center rounded-(--radius-04) border-none bg-transparent text-(--text-03) hover:bg-(--background-tint-02)"
      >
        <SvgChevronUp size={12} />
      </button>
      {/* raw-ok: same stepper control */}
      <button
        type="button"
        aria-label="Decrease"
        disabled={disabled}
        onClick={() => step(-1)}
        className="flex h-3 cursor-pointer items-center justify-center rounded-(--radius-04) border-none bg-transparent text-(--text-03) hover:bg-(--background-tint-02)"
      >
        <SvgChevronDown size={12} />
      </button>
    </div>
  );
}

/** 20px leading status glyph overlaid on an InputTypeIn, which exposes no
 *  leading-icon prop. Pairs with the `.limit-input` padding override. */
function InputLeadIcon({ children }: { children: ReactNode }) {
  return (
    <span className="pointer-events-none absolute top-1/2 left-[7px] z-[1] flex size-5 -translate-y-1/2 items-center justify-center p-[2px] text-(--text-04)">
      {children}
    </span>
  );
}

const digits = (s: string) => s.replace(/[^0-9]/g, "");

interface AutoEditLimitModalProps {
  onClose: () => void;
  count: number;
  threshold: number;
  cap: number;
  /** Explicit per-page threshold, null when using the workspace default. */
  ownThreshold: number | null;
  /** All-time commit count for the Total Edits summary. Null hides it. */
  totalEdits?: number | null;
  /** Admins get an editable Daily Auto-Edit Limit (mock annotation). */
  isAdmin: boolean;
  saving: boolean;
  /** Persists the per-page threshold, null clears back to the default.
   *  Resolves false when the save failed so the modal can stay open. */
  onSave: (value: number | null) => Promise<boolean>;
  /** Fires after an admin cap change lands so the host revalidates health. */
  onCapSaved?: () => void;
}

/** Auto-Edit Limit modal (mock 1899:349193): banded chrome (white header and
 *  footer over a tinted body), usage summary with chart, the editable
 *  per-page alert threshold, and the daily cap (admin-editable). Mount only
 *  while open so the drafts re-seed from the current policy each time. */
export function AutoEditLimitModal({
  onClose,
  count,
  threshold,
  cap,
  ownThreshold,
  totalEdits,
  isAdmin,
  saving,
  onSave,
  onCapSaved,
}: AutoEditLimitModalProps) {
  const [draft, setDraft] = useState(
    ownThreshold != null ? String(ownThreshold) : "",
  );
  const [capDraft, setCapDraft] = useState(String(cap));
  const [capBusy, setCapBusy] = useState(false);
  const [capError, setCapError] = useState<string | null>(null);
  const [thresholdError, setThresholdError] = useState(false);
  // The cap PUT must echo max_doc_chars, so admins pre-fetch the settings.
  const [ingest, setIngest] = useState<{ max_doc_chars: number } | null>(null);

  useEffect(() => {
    if (!isAdmin) return;
    let alive = true;
    apiFetch<{ max_doc_chars: number }>("/admin/ingest")
      .then((r) => {
        if (alive) setIngest(r);
      })
      .catch((e: unknown) => {
        if (alive) {
          setCapError(
            e instanceof Error ? e.message : "Couldn't load admin settings.",
          );
        }
      });
    return () => {
      alive = false;
    };
  }, [isAdmin]);

  const capEditable = isAdmin && ingest !== null;
  // A fetch failure leaves the field in the locked presentation, so admins
  // need the error line to say why it isn't editable.
  const capLoadFailed = isAdmin && ingest === null && capError !== null;

  const parsedThreshold = draft.trim() === "" ? null : Number(draft);
  const thresholdDirty = parsedThreshold !== ownThreshold;
  const parsedCap = capDraft.trim() === "" ? null : Number(capDraft);
  const capDirty = capEditable && parsedCap !== null && parsedCap !== cap;
  // An emptied cap field has no savable meaning (0 is the explicit "off"),
  // so it blocks Save instead of silently dropping the edit.
  const capCleared = capEditable && capDraft.trim() === "";
  const busy = saving || capBusy;
  const dirty = thresholdDirty || capDirty;
  const alertsNow =
    parsedThreshold !== null && parsedThreshold > 0 && count >= parsedThreshold;

  // Threshold first, then the cap: whichever fails leaves the modal open
  // with its own error line and its dirty flag intact, so retrying Save
  // re-runs only the failed half.
  const submit = async () => {
    if (!dirty || busy || capCleared) return;
    setThresholdError(false);
    if (thresholdDirty && !(await onSave(parsedThreshold))) {
      setThresholdError(true);
      return;
    }
    if (capDirty && ingest) {
      setCapBusy(true);
      setCapError(null);
      try {
        await apiFetch("/admin/ingest", {
          method: "PUT",
          body: JSON.stringify({
            max_doc_chars: ingest.max_doc_chars,
            auto_update_cap: parsedCap,
          }),
        });
        onCapSaved?.();
      } catch (e) {
        setCapError(
          e instanceof Error ? e.message : "Couldn't save the limit.",
        );
        return;
      } finally {
        setCapBusy(false);
      }
    }
    onClose();
  };

  // Dismissal is blocked while a save is in flight, so its failure can't
  // land on an unmounted modal and vanish.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, busy]);

  return (
    <>
      <div
        onClick={busy ? undefined : onClose}
        aria-hidden
        className="fixed inset-0 z-[90] bg-(--mask-03) backdrop-blur-[1px]"
      />
      <div
        role="dialog"
        aria-label="Auto-Edit Limit"
        className="fixed top-1/2 left-1/2 z-[95] flex w-[min(480px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-clip rounded-(--radius-16) border border-(--border-01) bg-(--background-tint-01) shadow-[0_2px_24px_0_var(--shadow-02),0_0_12px_1px_var(--shadow-01)]"
      >
        <Section
          alignItems="start"
          justifyContent="start"
          height="fit"
          gap={0.25}
          padding={1}
          className="relative border-b border-(--border-01) bg-(--background-tint-00)"
        >
          <span className="flex size-7 items-center justify-center p-[2px] text-(--text-04)">
            <SvgSliders size={24} />
          </span>
          <div className="flex min-w-0 flex-col">
            <span className="px-[2px]">
              <Text font="heading-h3" color="text-04">
                Auto-Edit Limit
              </Text>
            </span>
            <span className="px-[2px]">
              <Text font="secondary-body" color="text-03">
                Set daily auto-edit limits to control LLM usage.
              </Text>
            </span>
          </div>
          <div className="absolute top-2 right-2">
            <Button
              icon={SvgX}
              prominence="tertiary"
              tooltip="Close"
              disabled={busy}
              onClick={onClose}
            />
          </div>
        </Section>

        <div className="flex max-h-[580px] flex-col gap-4 overflow-y-auto px-4 pt-3 pb-4">
          <Section
            alignItems="stretch"
            height="fit"
            gap={0}
            className="rounded-(--radius-08)"
          >
            <Section
              flexDirection="row"
              justifyContent="start"
              alignItems="start"
              height="fit"
              gap={1}
            >
              <div className="min-w-0 flex-1 py-1 text-(--text-04)">
                <Content
                  sizePreset="main-ui"
                  variant="section"
                  title="Past Day Updates"
                  description="Last 24 hours"
                />
              </div>
              <Section
                width="fit"
                height="fit"
                alignItems="end"
                gap={0}
                padding={0.25}
                className="rounded-(--radius-04) bg-(--theme-blue-01)"
              >
                <span className="px-[2px]">
                  <Text font="main-ui-action" color="text-04">
                    {String(count)}
                  </Text>
                </span>
                <span className="px-[2px]">
                  <Text font="secondary-body" color="text-03">
                    Auto-Edits
                  </Text>
                </span>
              </Section>
              {totalEdits != null && (
                <Section
                  width="fit"
                  height="fit"
                  alignItems="end"
                  gap={0}
                  padding={0.25}
                >
                  <span className="px-[2px]">
                    <Text font="main-ui-action" color="text-02">
                      {String(totalEdits)}
                    </Text>
                  </span>
                  <span className="px-[2px]">
                    <Text font="secondary-body" color="text-02">
                      Total Edits
                    </Text>
                  </span>
                </Section>
              )}
            </Section>
            <div className="p-[2px]">
              <UsageBar count={count} threshold={threshold} cap={cap} />
            </div>
          </Section>

          <Divider paddingParallel="fit" paddingPerpendicular="fit" />

          <InputHorizontal
            title="Alert Threshold"
            description="Notify the page owner when daily auto-edits reaches this threshold."
          >
            <div className="flex w-full max-w-60 min-w-40 flex-col gap-1">
              <div className="limit-input relative w-full">
                <InputLeadIcon>
                  <SvgBell size={16} />
                </InputLeadIcon>
                <InputTypeIn
                  inputMode="numeric"
                  value={draft}
                  placeholder="Workspace default"
                  // Clamped as typed so the field always shows what Save
                  // will persist (the threshold cannot go over the limit).
                  onChange={(e) => {
                    const d = digits(e.target.value);
                    // The threshold cannot exceed the limit, but with no cap
                    // configured there is no ceiling to clamp against.
                    setDraft(
                      d === "" || cap <= 0
                        ? d
                        : String(Math.min(Number(d), cap)),
                    );
                  }}
                  rightChildren={
                    <div className="flex items-center">
                      <Stepper
                        value={draft}
                        onChange={setDraft}
                        max={cap > 0 ? cap : undefined}
                        disabled={busy}
                      />
                      <div className={draft === "" ? "invisible" : undefined}>
                        <Button
                          icon={SvgX}
                          prominence="internal"
                          size="sm"
                          tooltip="Clear"
                          onClick={() => setDraft("")}
                        />
                      </div>
                    </div>
                  }
                />
              </div>
              {alertsNow && (
                <span className="px-[2px]">
                  <Text font="secondary-body" color="text-03">
                    Page owner will be alerted.
                  </Text>
                </span>
              )}
              {thresholdError && (
                <span className="px-[2px] text-xs text-(--status-text-error-05)">
                  Couldn&apos;t save the alert threshold.
                </span>
              )}
            </div>
          </InputHorizontal>

          <InputHorizontal
            title="Daily Auto-Edit Limit"
            description="Pause auto-edits when daily auto-edits reaches this limit."
          >
            <div className="flex w-full max-w-60 min-w-40 flex-col gap-1">
              <div className="limit-input relative w-full">
                <InputLeadIcon>
                  <SvgPauseCircle size={16} />
                </InputLeadIcon>
                {capEditable ? (
                  <InputTypeIn
                    inputMode="numeric"
                    value={capDraft}
                    onChange={(e) => setCapDraft(digits(e.target.value))}
                    rightChildren={
                      <Stepper
                        value={capDraft}
                        onChange={setCapDraft}
                        disabled={busy}
                      />
                    }
                  />
                ) : (
                  <InputTypeIn
                    variant="disabled"
                    value={cap > 0 ? String(cap) : "No limit"}
                    onChange={() => undefined}
                  />
                )}
              </div>
              <span className="px-[2px]">
                <Text font="secondary-body" color="text-03">
                  {capCleared
                    ? "Enter a limit. 0 disables it."
                    : capEditable
                      ? "Workspace-wide limit. 0 disables it."
                      : capLoadFailed
                        ? "Couldn't load the limit settings."
                        : "Daily limit is set and locked by admins."}
                </Text>
              </span>
              {capError && (
                <span className="px-[2px] text-xs text-(--status-text-error-05)">
                  {capError}
                </span>
              )}
            </div>
          </InputHorizontal>
        </div>

        <Section
          flexDirection="row"
          justifyContent="end"
          height="fit"
          gap={0.5}
          padding={1}
          className="border-t border-(--border-01) bg-(--background-tint-00)"
        >
          <Button prominence="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="action"
            onClick={() => void submit()}
            disabled={busy || !dirty || capCleared}
          >
            {busy ? "Saving…" : "Save Changes"}
          </Button>
        </Section>
      </div>
    </>
  );
}
