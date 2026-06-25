"use client";

import { Button, Switch, Text } from "@onyx-ai/opal/components";
import { SvgEdit, SvgHistory, SvgX } from "@onyx-ai/opal/icons";
import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  getUpdatePolicy,
  patchUpdatePolicy,
  type UpdatePolicyResponse,
} from "@/lib/updatePolicy";
import { useUpdateHealth } from "@/lib/wiki";

import styles from "./UpdatePolicyPanel.module.css";

interface Props {
  path: string;
  onClose: () => void;
  fullHeight?: boolean;
  // When set, the activity row shows an "Update History" link that calls this.
  // Omit on surfaces with no history view (e.g. the folder explorer drawer).
  onShowHistory?: () => void;
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
}: Props) {
  const kind = path.endsWith(".md") ? "page" : "folder";

  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false); // first fetch succeeded
  const [policy, setPolicy] = useState<UpdatePolicyResponse | null>(null);
  const [pendingOn, setPendingOn] = useState<boolean | null>(null); // optimistic toggle
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  // Live slider value while dragging the per-page warning threshold; null until
  // health loads (then seeded with the effective threshold).
  const [sliderVal, setSliderVal] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Auto-update health (24h count, effective threshold, cap) as a live poll, so
  // the count + slider reflect ingestion writes without reopening the panel. A
  // failure here never blocks the policy card — null hides the activity row +
  // slider.
  const { health, refresh: refreshHealth } = useUpdateHealth(path);
  // Seed the slider from the effective threshold once per page. Tracking the
  // health's own path (not a poll tick) keeps a 15s revalidation from yanking
  // the thumb mid-drag while still re-seeding when the panel switches pages.
  const seededFor = useRef<string | null>(null);
  useEffect(() => {
    if (health && seededFor.current !== health.path) {
      seededFor.current = health.path;
      setSliderVal(health.threshold_24h);
    }
  }, [health]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setLoaded(false);
    setError(null);
    setEditing(false);
    setSliderVal(null);
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
  const ownInstruction = policy?.explicit?.update_instruction ?? "";
  const effInstruction = policy?.effective.update_instruction ?? "";
  // Per-page warning threshold: explicit value set on this page (null = using
  // the workspace default). The slider's max is the admin cap.
  const ownThreshold = policy?.explicit?.warn_update_threshold ?? null;
  const thresholdSetHere = ownThreshold != null;
  const sliderMax = health && health.cap_24h > 0 ? health.cap_24h : 100;

  async function save(
    patch: Parameters<typeof patchUpdatePolicy>[1],
    after?: () => void,
  ) {
    setSaving(true);
    setError(null);
    try {
      setPolicy(await patchUpdatePolicy(path, patch));
      after?.();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
      setPendingOn(null);
    }
  }

  // Persist the slider's value as the page's explicit threshold (or clear it
  // back to the default), then revalidate health and re-seed the slider so the
  // effective value shown is accurate (notably when clearing to the default).
  function saveThreshold(value: number | null) {
    void save({ warn_update_threshold: value }, async () => {
      const fresh = await refreshHealth();
      if (fresh) setSliderVal(fresh.threshold_24h);
    });
  }

  // "Auto-Update Wiki" ON = ingestion auto-update enabled (NOT disabled).
  // Patches only the disable field, so the instruction's inheritance is untouched.
  function onToggle(on: boolean) {
    setPendingOn(on);
    void save({ ingestion_auto_update_disabled: !on });
  }

  const switchOn = pendingOn ?? !effDisabled;

  return (
    <div className={`${styles.panel} ${fullHeight ? styles.fullHeight : ""}`}>
      <div className={styles.headerRow}>
        <div className={styles.headerTitle}>
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

      <div className={styles.scroll}>
        {loading ? (
          <div className={styles.muted}>Loading…</div>
        ) : !loaded ? (
          // Load failed: never render the card — its default values would
          // overwrite a real policy if the user interacted with it.
          <div className={styles.error}>
            {error ?? "Couldn't load the update policy."}
          </div>
        ) : (
          <div className={styles.card}>
            <div className={styles.row}>
              <div className={styles.rowText}>
                <Text font="main-content-emphasis" color="text-04">
                  Auto-Update Wiki
                </Text>
                <span className={styles.desc}>
                  Onyx will periodically scan ingested data sources and update
                  relevant wiki content.
                </span>
                {disableSetHere ? (
                  <div className={styles.originRow}>
                    <span className={styles.origin}>Set on this {kind}</span>
                    <Button
                      prominence="tertiary"
                      size="sm"
                      disabled={saving}
                      onClick={() =>
                        save({ ingestion_auto_update_disabled: null })
                      }
                    >
                      Reset to inherited
                    </Button>
                  </div>
                ) : (
                  <span className={styles.origin}>
                    {effDisabled
                      ? "Inherited — off (from a parent folder)"
                      : "Inherited — on (default)"}
                  </span>
                )}
              </div>
              <Switch
                checked={switchOn}
                disabled={saving}
                onCheckedChange={onToggle}
              />
            </div>

            <div className={styles.row}>
              <div className={styles.rowText}>
                <Text font="main-content-emphasis" color="text-04">
                  Update Instructions
                </Text>
                <span className={styles.desc}>
                  Add instructions on how this {kind} should be updated.
                </span>
              </div>
              {!editing && (
                <Button
                  icon={SvgEdit}
                  prominence="tertiary"
                  size="sm"
                  tooltip="Edit instructions"
                  onClick={() => {
                    setDraft(ownInstruction);
                    setEditing(true);
                  }}
                />
              )}
            </div>

            {!editing && ownInstruction && (
              <div className={styles.instruction}>{ownInstruction}</div>
            )}
            {!editing && !ownInstruction && effInstruction && (
              // No instruction of its own — show the one inherited from a parent.
              <div className={styles.instruction}>
                <span className={styles.origin}>
                  Inherited from a parent folder
                </span>
                {effInstruction}
              </div>
            )}

            {editing && (
              <div>
                <textarea
                  className={styles.textarea}
                  value={draft}
                  autoFocus
                  placeholder={`How should this ${kind} be updated?`}
                  onChange={(e) => setDraft(e.target.value)}
                />
                <div className={styles.composeRow}>
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

            {health !== null && (
              <div className={`${styles.row} ${styles.activityRow}`}>
                <div className={styles.rowText}>
                  <Text font="main-content-emphasis" color="text-04">
                    {`${health.count_24h} Auto Update${health.count_24h === 1 ? "" : "s"}`}
                  </Text>
                  <span className={styles.desc}>in the past 24 hours</span>
                </div>
                {onShowHistory && (
                  <Button
                    icon={SvgHistory}
                    prominence="tertiary"
                    size="sm"
                    onClick={onShowHistory}
                  >
                    Update History
                  </Button>
                )}
              </div>
            )}

            {kind === "page" &&
              !effDisabled &&
              health !== null &&
              sliderVal !== null && (
                <div className={styles.warnRow}>
                  <div className={styles.warnHeader}>
                    <Text font="main-content-emphasis" color="text-04">
                      Warn after
                    </Text>
                    <span className={styles.warnValue}>
                      {sliderVal === 0
                        ? "Every auto-update"
                        : `${sliderVal} update${sliderVal === 1 ? "" : "s"} / day`}
                    </span>
                  </div>
                  <input
                    type="range"
                    className={styles.slider}
                    min={0}
                    max={sliderMax}
                    step={1}
                    value={sliderVal}
                    disabled={saving}
                    onChange={(e) => setSliderVal(Number(e.target.value))}
                    onPointerUp={() => saveThreshold(sliderVal)}
                    onKeyUp={() => saveThreshold(sliderVal)}
                  />
                  <div className={styles.warnScale}>
                    <span>0</span>
                    <span>{sliderMax}</span>
                  </div>
                  {thresholdSetHere ? (
                    <div className={styles.originRow}>
                      <span className={styles.origin}>Set on this page</span>
                      <Button
                        prominence="tertiary"
                        size="sm"
                        disabled={saving}
                        onClick={() => saveThreshold(null)}
                      >
                        Use workspace default
                      </Button>
                    </div>
                  ) : (
                    <span className={styles.origin}>
                      Using the workspace default
                    </span>
                  )}
                </div>
              )}
          </div>
        )}
        {/* Save errors sit below the card; load errors render in the slot above. */}
        {loaded && error && <div className={styles.error}>{error}</div>}
      </div>
    </div>
  );
}
