"use client";

import { Button, Divider, Switch, Text } from "@onyx-ai/opal/components";
import {
  SvgAddLines,
  SvgBell,
  SvgExpand,
  SvgHistory,
  SvgPauseCircle,
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
import { useUpdateHealth } from "@/lib/wiki";

interface Props {
  path: string;
  // Renders the close control when set. Omit when the panel is hosted inline
  // (folder page column, side-panel tab) rather than as a dismissable drawer.
  onClose?: () => void;
  fullHeight?: boolean;
  // When set, the history card shows an "Update History" link that calls
  // this. Omit on surfaces with no history view (e.g. the folder drawer).
  onShowHistory?: () => void;
}

function errorMessage(e: unknown): string {
  if (e instanceof ApiError && e.status === 403) {
    return "You don't have permission to change this.";
  }
  return e instanceof Error ? e.message : "Something went wrong.";
}

interface PolicyRowProps {
  title: string;
  description: string;
  /** Leads the row with the AI sparkle glyph (the mock marks only AI rows). */
  sparkle?: boolean;
  origin?: React.ReactNode;
  control: React.ReactNode;
}

/** One labelled control row of the policy card (mock 1807:54673). */
function PolicyRow({
  title,
  description,
  sparkle,
  origin,
  control,
}: PolicyRowProps) {
  return (
    <div className="flex items-start justify-between gap-2">
      <div className="flex min-w-0 flex-1 gap-1">
        {sparkle && (
          <SvgSparkle size={16} className="mt-0.5 shrink-0 text-(--text-04)" />
        )}
        <div className="flex min-w-0 flex-col">
          <Text font="main-ui-action" color="text-04">
            {title}
          </Text>
          <Text font="secondary-body" color="text-03">
            {description}
          </Text>
          {origin}
        </div>
      </div>
      <div className="shrink-0 pt-0.5">{control}</div>
    </div>
  );
}

export function UpdatePolicyPanel({
  path,
  onClose,
  fullHeight,
  onShowHistory,
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
  // Auto-update health (24h count, effective threshold, cap) as a live poll,
  // so the history card reflects ingestion writes without reopening the
  // panel. A failure here never blocks the policy card, null just hides
  // the history card.
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

  // Effective = what's actually in force (incl. inheritance); explicit = the row
  // set on exactly this path. A field is "set here" only when explicit carries a
  // value for it; otherwise it's inherited from an ancestor / the default.
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
      // Any policy change can move the health facts (auto-update on/off, the
      // threshold), so revalidate the shared update-health cache now instead of
      // waiting for the poll — the page-view banner reuses the same key and
      // updates in lockstep.
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
  // Patches only the disable field, so the instruction's inheritance is untouched.
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

  function originLine(
    setHere: boolean,
    effOn: boolean,
    reset: () => void,
  ): React.ReactNode {
    return setHere ? (
      <div className="mt-1 flex items-center gap-2">
        <span className="text-xs text-(--text-03)">Set on this {kind}</span>
        <Button
          prominence="tertiary"
          size="sm"
          disabled={saving}
          onClick={reset}
        >
          Reset to inherited
        </Button>
      </div>
    ) : (
      <span className="mt-0.5 text-xs text-(--text-03)">
        {effOn
          ? "Inherited — on (from a parent folder)"
          : "Inherited — off (default)"}
      </span>
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
            <div className="flex flex-col gap-2 rounded-(--radius-12) border border-(--border-01) p-3">
              <PolicyRow
                title="AI Auto-Edits"
                description={`Let AI update/organize this ${kind} on its own.`}
                sparkle
                origin={originLine(
                  aiManagedSetHere,
                  effAiManaged,
                  () => void save({ ai_management_allowed: null }),
                )}
                control={
                  <Switch
                    checked={aiSwitchOn}
                    disabled={saving}
                    onCheckedChange={onToggleAiManaged}
                  />
                }
              />
              <PolicyRow
                title="Update"
                description="Periodically scan ingested data sources to add relevant new information."
                origin={originLine(
                  disableSetHere,
                  !effDisabled,
                  () => void save({ ingestion_auto_update_disabled: null }),
                )}
                control={
                  <Switch
                    checked={switchOn}
                    disabled={saving}
                    onCheckedChange={onToggle}
                  />
                }
              />
              <PolicyRow
                title="Organize"
                description={`Reorganize, move, and/or merge content in this ${kind} when needed.`}
                origin={
                  <span className="mt-0.5 text-xs text-(--text-03)">
                    Coming soon
                  </span>
                }
                control={<Switch checked={false} disabled />}
              />

              <Divider paddingParallel="fit" paddingPerpendicular="fit" />

              <PolicyRow
                title="Page Instructions"
                description={`Add instructions on how this ${kind} should be updated.`}
                control={
                  !editing ? (
                    <Button
                      icon={SvgAddLines}
                      prominence="secondary"
                      tooltip="Edit instructions"
                      onClick={() => {
                        setDraft(ownInstruction);
                        setEditing(true);
                      }}
                    />
                  ) : null
                }
              />

              {!editing && ownInstruction && (
                <div className="text-[13px] whitespace-pre-wrap text-(--text-05)">
                  {ownInstruction}
                </div>
              )}
              {!editing && !ownInstruction && effInstruction && (
                // No instruction of its own, so show the inherited one.
                <div className="text-[13px] whitespace-pre-wrap text-(--text-05)">
                  <span className="block text-xs text-(--text-03)">
                    Inherited from a parent folder
                  </span>
                  {effInstruction}
                </div>
              )}

              {editing && (
                <div>
                  {/* raw-ok: InputTextArea's opal-input chrome duplicates the card border. */}
                  <textarea
                    className="box-border min-h-24 w-full resize-y rounded-(--radius-04) border border-(--border-01) bg-(--background-tint-00) p-2 font-[inherit] text-[13px] text-(--text-05) outline-none focus:border-(--border-05)"
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
                      icon={SvgExpand}
                      prominence="tertiary"
                      size="sm"
                      tooltip="Auto-edit limits"
                      onClick={() => setLimitOpen(true)}
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
                      {overCap
                        ? "Daily auto-edit limit reached. Updates will resume within 24 hours."
                        : health.cap_24h > 0
                          ? "Approaching daily auto-edit limit. Updates will pause when the limit is reached."
                          : "Auto-updating frequently."}
                    </Text>
                  </div>
                )}
                {onShowHistory && (
                  <div className="flex justify-end px-1 pb-1">
                    <Button
                      rightIcon={SvgHistory}
                      prominence="tertiary"
                      size="sm"
                      onClick={onShowHistory}
                    >
                      Update History
                    </Button>
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
