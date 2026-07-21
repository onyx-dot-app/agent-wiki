"use client";

import {
  Button,
  Divider,
  InputTextArea,
  Switch,
  Text,
  Tooltip,
} from "@onyx-ai/opal/components";
import { InputHorizontal, Section } from "@onyx-ai/opal/layouts";
import {
  SvgAddLines,
  SvgAlertTriangle,
  SvgBell,
  SvgExpand,
  SvgFold,
  SvgHistory,
  SvgSparkle,
  SvgX,
} from "@onyx-ai/opal/icons";
import { useEffect, useState, type ReactNode } from "react";

import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
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
  /** Whether the host's version list is showing. */
  historyOpen?: boolean;
  /** The version list itself, rendered inside the history card while
   * `historyOpen` (mock 1855:273363 expands the card in place). */
  historyList?: ReactNode;
  /** All-time commit count for the "Total Edits" summary column. Null while
   * the host hasn't loaded history yet, which hides the column. */
  totalEdits?: number | null;
}

function capNote(health: UpdateHealth): string {
  if (health.cap_24h > 0 && health.count_24h >= health.cap_24h) {
    return health.cap_resets_at
      ? `Daily auto-edit limit reached. Updates will resume at ${absoluteTime(health.cap_resets_at)}.`
      : "Daily auto-edit limit reached. Updates will resume within 24 hours.";
  }
  if (health.cap_24h > 0) {
    return "Approaching daily auto-edit limit. Updates will pause when the limit is reached.";
  }
  if (health.threshold_24h > 0 && health.count_24h >= health.threshold_24h) {
    return `Reached the alert threshold of ${health.threshold_24h} auto-edits in 24 hours.`;
  }
  return "Auto-updating frequently.";
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
  historyList,
  totalEdits,
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
  const { user } = useAuth();

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
  ): ReactNode {
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

      <Section
        justifyContent="start"
        alignItems="stretch"
        height="auto"
        gap={0.25}
        className="scroll-y-hidden min-h-0 flex-1 overflow-y-auto"
      >
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
            {/* Mock nesting (1790:52546): card p-1 gap-1, toggle group p-2
                gap-2 with sub-rows indented past the master row's icon,
                divider, then the instructions section p-2. */}
            <Section
              justifyContent="start"
              alignItems="stretch"
              height="fit"
              gap={0.25}
              padding={0.25}
              className="group/policy rounded-(--radius-12) border border-(--border-01)"
            >
              <div className="flex flex-col gap-2 p-2">
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
                <div className="flex flex-col gap-2 pl-6">
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
                      {/* The span keeps hover alive: a disabled control
                          swallows pointer events, so the tooltip would never
                          fire on it. */}
                      <span className="inline-flex">
                        <Switch checked={false} disabled />
                      </span>
                    </Tooltip>
                  </InputHorizontal>
                </div>
              </div>

              <Divider />

              <div className="flex flex-col gap-1 p-2">
                {/* Collapsed, the row's description is the instruction when
                    one exists (mock 1855:273683). While the editor is open
                    the row shows the generic hint (mock 1855:273690). */}
                <InputHorizontal
                  icon={SvgAddLines}
                  title="Page Instructions"
                  description={
                    editing
                      ? `Instruct the wiki on how to update this ${kind}.`
                      : ownInstruction ||
                        effInstruction ||
                        `Instruct the wiki on how to update this ${kind}.`
                  }
                >
                  <Button
                    icon={editing ? SvgFold : SvgExpand}
                    prominence="tertiary"
                    size="md"
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
                  // Implicit save (the mock has no Save/Cancel controls):
                  // blurring the editor persists a changed draft, collapsing
                  // just hides it. A failed save reopens the editor with the
                  // draft intact so the text can't be lost to a collapse.
                  <div className="instructions-editor">
                    <InputTextArea
                      rows={5}
                      resizable
                      value={draft}
                      autoFocus
                      placeholder={`How should this ${kind} be updated?`}
                      onChange={(e) => setDraft(e.target.value)}
                      onBlur={() => {
                        const trimmed = draft.trim();
                        if (trimmed === ownInstruction.trim()) return;
                        void save({ update_instruction: trimmed || null }).then(
                          (ok) => {
                            if (!ok) setEditing(true);
                          },
                        );
                      }}
                    />
                  </div>
                )}
              </div>
            </Section>

            {health !== null && (
              <Section
                justifyContent="start"
                alignItems="stretch"
                height={historyOpen ? "auto" : "fit"}
                gap={0}
                padding={0.25}
                className={`rounded-(--radius-12) border ${
                  historyOpen ? "min-h-0 flex-1" : ""
                } ${historyCardChrome}`}
              >
                {/* Collapsed content keeps its 8px inset via this p-1; the
                    expanded list below sits at the card's own 4px (mock
                    1855:273693). */}
                <Section
                  justifyContent="start"
                  alignItems="stretch"
                  height="fit"
                  gap={0.25}
                  padding={0.25}
                  className="shrink-0"
                >
                  <div className="flex items-start gap-3">
                    <div className="flex min-w-0 flex-1 items-start gap-4">
                      <div className="flex min-w-0 flex-1 gap-1">
                        <span className="flex size-5 shrink-0 items-center justify-center text-(--text-04)">
                          <SvgHistory size={16} />
                        </span>
                        <div className="flex min-w-0 flex-col">
                          <Text font="main-ui-action" color="text-04">
                            Update History
                          </Text>
                          <Text font="secondary-body" color="text-03">
                            Last 24 hours
                          </Text>
                        </div>
                      </div>
                      <div className="flex shrink-0 flex-col items-end">
                        <div className="flex min-h-5 items-center gap-0.5">
                          <Text font="main-ui-action" color="text-04">
                            {String(health.count_24h)}
                          </Text>
                          {(overCap || nearCap) && (
                            <Tooltip tooltip={capNote(health)} side="top">
                              <span className="flex size-4 items-center justify-center text-(--text-04)">
                                {overCap ? (
                                  <SvgAlertTriangle size={12} />
                                ) : (
                                  <SvgBell size={12} />
                                )}
                              </span>
                            </Tooltip>
                          )}
                        </div>
                        <Text font="secondary-body" color="text-03">
                          Auto-Edits
                        </Text>
                      </div>
                      {totalEdits != null && (
                        <div className="flex shrink-0 flex-col items-end">
                          <div className="flex min-h-5 items-center">
                            <Text font="main-ui-action" color="text-04">
                              {String(totalEdits)}
                            </Text>
                          </div>
                          <Text font="secondary-body" color="text-03">
                            Total Edits
                          </Text>
                        </div>
                      )}
                    </div>
                    {onShowHistory && (
                      <Button
                        icon={historyOpen ? SvgFold : SvgExpand}
                        prominence="tertiary"
                        size="md"
                        tooltip="Version history"
                        onClick={onShowHistory}
                      />
                    )}
                  </div>
                  <div className="shrink-0">
                    {kind === "page" &&
                    health.can_manage &&
                    health.cap_24h > 0 ? (
                      // raw-ok: the mock's dialog trigger is the usage chart region itself. SelectCard, Opal's clickable card, is a selection-state div with no native button semantics, so a bare button keeps aria and keyboard.
                      <button
                        type="button"
                        aria-label="Auto-edit limits"
                        onClick={() => setLimitOpen(true)}
                        className="block w-full cursor-pointer rounded-(--radius-08) border-none bg-transparent p-[2px] text-left hover:bg-(--background-tint-02)"
                      >
                        <UsageBar
                          count={health.count_24h}
                          threshold={health.threshold_24h}
                          cap={health.cap_24h}
                        />
                      </button>
                    ) : (
                      <div className="rounded-(--radius-08) p-[2px]">
                        <UsageBar
                          count={health.count_24h}
                          threshold={health.threshold_24h}
                          cap={health.cap_24h}
                        />
                      </div>
                    )}
                  </div>
                  {(overCap || nearCap) && (
                    <div className="flex shrink-0 items-start gap-0.5">
                      <span className="flex size-4 shrink-0 items-center justify-center text-(--text-04)">
                        {overCap ? (
                          <SvgAlertTriangle size={12} />
                        ) : (
                          <SvgBell size={12} />
                        )}
                      </span>
                      <span className="px-[2px]">
                        <Text
                          font="secondary-body"
                          color={overCap ? "text-04" : "text-03"}
                        >
                          {capNote(health)}
                        </Text>
                      </span>
                    </div>
                  )}
                </Section>
                {historyOpen && historyList}
              </Section>
            )}
          </>
        )}
        {/* Save errors sit below the cards. Load errors render in the slot above. */}
        {loaded && error && (
          <div className="py-1 text-xs text-(--status-text-error-05)">
            {error}
          </div>
        )}
      </Section>

      {limitOpen && health && (
        <AutoEditLimitModal
          onClose={() => setLimitOpen(false)}
          count={health.count_24h}
          threshold={health.threshold_24h}
          cap={health.cap_24h}
          ownThreshold={ownThreshold}
          totalEdits={totalEdits}
          isAdmin={!!user?.is_admin}
          saving={saving}
          onSave={(value) => save({ warn_update_threshold: value })}
          onCapSaved={() => void refreshHealth()}
        />
      )}
    </div>
  );
}
