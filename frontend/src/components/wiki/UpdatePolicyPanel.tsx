"use client";

import {
  Button,
  Divider,
  InputTextArea,
  SelectButton,
  Switch,
  Text,
  Tooltip,
} from "@onyx-ai/opal/components";
import { InputHorizontal } from "@onyx-ai/opal/layouts";
import {
  SvgBell,
  SvgExpand,
  SvgHistory,
  SvgPauseCircle,
  SvgSliders,
  SvgSparkle,
  SvgX,
} from "@onyx-ai/opal/icons";
import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  AutoEditLimitModal,
  UsageBar,
} from "@/components/wiki/AutoEditLimitModal";
import {
  getUpdatePolicy,
  patchUpdatePolicy,
  type UpdatePolicyResponse,
} from "@/lib/updatePolicy";
import { useUpdateHealth } from "@/lib/wiki/hooks";
import type { UpdateHealth } from "@/lib/wiki/types";
import { absoluteTime } from "@/lib/time";

interface Props {
  path: string;
  /** Renders the drawer chrome (title bar + close) when set. Omit when the
   * panel is hosted inline (folder page column, side-panel tab). */
  onClose?: () => void;
  fullHeight?: boolean;
  /** When set, the history card's expander toggles the host's version list.
   * Omit on surfaces with no history view (e.g. the folder drawer). */
  onShowHistory?: () => void;
  /** Whether the host's version list is showing (tints the expander). */
  historyOpen?: boolean;
}

function capNote(health: UpdateHealth): string {
  if (health.cap_24h > 0 && health.count_24h >= health.cap_24h) {
    return health.cap_resets_at
      ? `Daily auto-edit limit reached. Updates will resume at ${absoluteTime(health.cap_resets_at)}.`
      : "Daily auto-edit limit reached. Updates will resume within 24 hours.";
  }
  return health.cap_24h > 0
    ? "Approaching daily auto-edit limit. Updates will pause when the limit is reached."
    : "Auto-updating frequently.";
}

function errorMessage(e: unknown): string {
  if (e instanceof ApiError && e.status === 403) {
    return "You don't have permission to change this.";
  }
  return e instanceof Error ? e.message : "Something went wrong.";
}

export function UpdatePolicyPanel({
  path,
  onClose,
  fullHeight,
  onShowHistory,
  historyOpen,
}: Props) {
  const kind = path.endsWith(".md") ? "page" : "folder";

  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false); // first fetch succeeded
  const [policy, setPolicy] = useState<UpdatePolicyResponse | null>(null);
  const [pendingOn, setPendingOn] = useState<boolean | null>(null); // optimistic toggle
  const [pendingAiOn, setPendingAiOn] = useState<boolean | null>(null); // optimistic AI toggle
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [limitOpen, setLimitOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Live health poll backs the history card. A failure never blocks the
  // policy card, null just hides the history card.
  const { health, refresh: refreshHealth } = useUpdateHealth(path);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setLoaded(false);
    setError(null);
    setEditing(false);
    getUpdatePolicy(path)
      .then((r) => {
        if (alive) {
          setPolicy(r);
          setLoaded(true);
        }
      })
      .catch((e) => {
        if (alive) setError(errorMessage(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [path]);

  // A field is "set here" only when explicit carries a value for it,
  // otherwise it's inherited from an ancestor or the default.
  const effDisabled = policy?.effective.ingestion_auto_update_disabled ?? false;
  const disableSetHere =
    policy?.explicit?.ingestion_auto_update_disabled != null;
  const effAiManaged = policy?.effective.ai_management_allowed ?? false;
  const aiManagedSetHere = policy?.explicit?.ai_management_allowed != null;
  const ownInstruction = policy?.explicit?.update_instruction ?? "";
  const effInstruction = policy?.effective.update_instruction ?? "";
  // Per-page warning threshold, null = using the workspace default.
  const ownThreshold = policy?.explicit?.warn_update_threshold ?? null;

  async function save(
    patch: Parameters<typeof patchUpdatePolicy>[1],
    after?: () => void,
  ): Promise<boolean> {
    setSaving(true);
    setError(null);
    try {
      setPolicy(await patchUpdatePolicy(path, patch));
      // A policy change can move the health facts (auto-update on/off, the
      // threshold), so revalidate the shared update-health cache now. The
      // page-view banner reuses the same key and updates in lockstep.
      void refreshHealth();
      after?.();
      return true;
    } catch (e) {
      setError(errorMessage(e));
      return false;
    } finally {
      setSaving(false);
      setPendingOn(null);
      setPendingAiOn(null);
    }
  }

  // "Update" ON = ingestion auto-update enabled (NOT disabled).
  function onToggle(on: boolean) {
    setPendingOn(on);
    void save({ ingestion_auto_update_disabled: !on });
  }

  const switchOn = pendingOn ?? !effDisabled;

  // "AI Auto-Edits" ON = ai_management_allowed (stored positively, no inversion).
  function onToggleAiManaged(on: boolean) {
    setPendingAiOn(on);
    void save({ ai_management_allowed: on });
  }

  const aiSwitchOn = pendingAiOn ?? effAiManaged;

  // The switch carries the inheritance story out of the card body: origin
  // in its tooltip, and a reset affordance that appears only while the card
  // is hovered (the mock shows bare toggle rows).
  function policySwitch(
    checked: boolean,
    onChange: (on: boolean) => void,
    setHere: boolean,
    reset: () => void,
  ): React.ReactNode {
    const origin = setHere
      ? `Set on this ${kind}`
      : checked
        ? "Inherited from a parent folder"
        : "Inherited (default)";
    return (
      <div className="flex flex-col items-end gap-1">
        <Tooltip tooltip={origin} side="left">
          <Switch
            checked={checked}
            disabled={saving}
            onCheckedChange={onChange}
          />
        </Tooltip>
        {setHere && (
          <span className="opacity-0 transition-opacity group-hover/policy:opacity-100">
            <Button
              prominence="tertiary"
              size="sm"
              disabled={saving}
              onClick={reset}
            >
              Reset
            </Button>
          </span>
        )}
      </div>
    );
  }

  // Health state drives the history card's chrome (mock 1790:52516/52531).
  const overCap =
    !!health && health.cap_24h > 0 && health.count_24h >= health.cap_24h;
  const nearCap =
    !!health &&
    !overCap &&
    health.count_24h > 0 &&
    health.count_24h >= health.threshold_24h;
  const historyCardChrome = overCap
    ? "border-(--status-warning-02) bg-(--status-warning-00)"
    : nearCap
      ? "border-(--theme-amber-02) bg-(--theme-amber-01)"
      : "border-(--border-01)";

  return (
    <div
      className={`flex min-h-0 flex-col gap-2 ${
        fullHeight ? "h-full w-full" : ""
      } ${onClose ? "w-full rounded-(--radius-12) bg-(--background-tint-01) p-2" : ""}`}
    >
      {/* Title + close only in drawer hosts. Panel/inline hosts start straight
          at the cards (mocks 1790:52468 and 1673:32813). */}
      {onClose && (
        <div className="flex shrink-0 items-center gap-1 p-1">
          <div className="min-w-0 flex-1">
            <Text font="main-ui-action" color="text-04">
              Update Policy
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
      )}

      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
        {loading ? (
          <div className="p-2 text-[13px] text-(--text-03)">Loading…</div>
        ) : !loaded ? (
          // Load failed: never render the cards, the policy card's default
          // values would overwrite a real policy if the user interacted with it.
          <div className="py-1 text-xs text-(--status-text-error-05)">
            {error ?? "Couldn't load the update policy."}
          </div>
        ) : (
          <>
            <div className="group/policy flex flex-col gap-2 rounded-(--radius-12) border border-(--border-01) p-3">
              <InputHorizontal
                icon={SvgSparkle}
                title="AI Auto-Edits"
                description={`Let AI update/organize this ${kind} on its own.`}
              >
                {policySwitch(
                  aiSwitchOn,
                  onToggleAiManaged,
                  aiManagedSetHere,
                  () => void save({ ai_management_allowed: null }),
                )}
              </InputHorizontal>
              <InputHorizontal
                title="Update"
                description="Periodically scan ingested data sources to add relevant new information."
              >
                {policySwitch(
                  switchOn,
                  onToggle,
                  disableSetHere,
                  () => void save({ ingestion_auto_update_disabled: null }),
                )}
              </InputHorizontal>
              <InputHorizontal
                title="Organize"
                description={`Reorganize, move, and/or merge content in this ${kind} when needed.`}
              >
                <Tooltip tooltip="Coming soon" side="left">
                  {/* The span keeps hover alive: a disabled control swallows
                      pointer events, so the tooltip would never fire on it. */}
                  <span className="inline-flex">
                    <Switch checked={false} disabled />
                  </span>
                </Tooltip>
              </InputHorizontal>

              <Divider paddingParallel="fit" paddingPerpendicular="fit" />

              {/* Collapsed, the row's description is the instruction when one
                  exists (mock 1855:273683). The expander opens the editor. */}
              <InputHorizontal
                title="Page Instructions"
                description={
                  ownInstruction ||
                  effInstruction ||
                  `Instruct the wiki on how to update this ${kind}.`
                }
              >
                <SelectButton
                  icon={SvgExpand}
                  state={editing ? "selected" : "empty"}
                  tooltip={editing ? "Collapse" : "Edit instructions"}
                  onClick={() => {
                    if (editing) {
                      setEditing(false);
                      return;
                    }
                    setDraft(ownInstruction);
                    setEditing(true);
                  }}
                />
              </InputHorizontal>

              {!editing && !ownInstruction && effInstruction && (
                <Text font="secondary-body" color="text-03">
                  Inherited from a parent folder
                </Text>
              )}

              {editing && (
                <div>
                  <InputTextArea
                    rows={4}
                    resizable
                    value={draft}
                    autoFocus
                    placeholder={`How should this ${kind} be updated?`}
                    onChange={(e) => setDraft(e.target.value)}
                  />
                  <div className="flex justify-end gap-2">
                    <Button
                      prominence="tertiary"
                      size="sm"
                      disabled={saving}
                      onClick={() => setEditing(false)}
                    >
                      Cancel
                    </Button>
                    <Button
                      variant="action"
                      size="sm"
                      disabled={saving}
                      onClick={() =>
                        void save(
                          { update_instruction: draft.trim() || null },
                          () => setEditing(false),
                        )
                      }
                    >
                      {saving ? "Saving…" : "Save"}
                    </Button>
                  </div>
                </div>
              )}
            </div>

            {health !== null && (
              <div
                className={`flex flex-col gap-1 rounded-(--radius-12) border p-2 ${historyCardChrome}`}
              >
                <div className="flex items-start gap-3 p-1">
                  <div className="flex min-w-0 flex-1 gap-1">
                    <SvgHistory
                      size={16}
                      className="mt-0.5 shrink-0 text-(--text-04)"
                    />
                    <div className="flex min-w-0 flex-col">
                      <Text font="main-ui-action" color="text-04">
                        Update History
                      </Text>
                      <Text font="secondary-body" color="text-03">
                        Last 24 hours
                      </Text>
                    </div>
                  </div>
                  <div className="flex flex-col items-end">
                    <Text font="main-ui-action" color="text-04">
                      {String(health.count_24h)}
                    </Text>
                    <Text font="secondary-body" color="text-03">
                      Auto-Edits
                    </Text>
                  </div>
                  {kind === "page" && (
                    <Button
                      icon={SvgSliders}
                      prominence="tertiary"
                      size="sm"
                      tooltip="Auto-edit limits"
                      onClick={() => setLimitOpen(true)}
                    />
                  )}
                  {onShowHistory && (
                    <SelectButton
                      icon={SvgExpand}
                      state={historyOpen ? "selected" : "empty"}
                      tooltip="Version history"
                      onClick={onShowHistory}
                    />
                  )}
                </div>
                <div className="px-1 pb-1">
                  <UsageBar
                    count={health.count_24h}
                    threshold={health.threshold_24h}
                    cap={health.cap_24h}
                  />
                </div>
                {(overCap || nearCap) && (
                  <div className="flex items-start gap-1 px-1 pb-1">
                    {overCap ? (
                      <SvgPauseCircle
                        size={12}
                        className="mt-0.5 shrink-0 text-(--text-04)"
                      />
                    ) : (
                      <SvgBell
                        size={12}
                        className="mt-0.5 shrink-0 text-(--text-04)"
                      />
                    )}
                    <Text font="secondary-body" color="text-04">
                      {capNote(health)}
                    </Text>
                  </div>
                )}
              </div>
            )}
          </>
        )}
        {/* Save errors sit below the cards. Load errors render in the slot above. */}
        {loaded && error && (
          <div className="py-1 text-xs text-(--status-text-error-05)">
            {error}
          </div>
        )}
      </div>

      {limitOpen && health && (
        <AutoEditLimitModal
          onClose={() => setLimitOpen(false)}
          count={health.count_24h}
          threshold={health.threshold_24h}
          cap={health.cap_24h}
          ownThreshold={ownThreshold}
          saving={saving}
          onSave={(value) => save({ warn_update_threshold: value })}
        />
      )}
    </div>
  );
}
