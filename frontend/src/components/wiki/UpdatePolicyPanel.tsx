"use client";

import { Button, Switch, Text } from "@onyx-ai/opal/components";
import { SvgEdit, SvgX } from "@onyx-ai/opal/icons";
import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import {
  getUpdatePolicy,
  setUpdatePolicy,
  type UpdatePolicyResponse,
} from "@/lib/updatePolicy";

import styles from "./UpdatePolicyPanel.module.css";

interface Props {
  path: string;
  onClose: () => void;
  fullHeight?: boolean;
}

function errorMessage(e: unknown): string {
  if (e instanceof ApiError && e.status === 403) {
    return "You don't have permission to change this.";
  }
  return e instanceof Error ? e.message : "Something went wrong.";
}

export function UpdatePolicyPanel({ path, onClose, fullHeight }: Props) {
  const kind = path.endsWith(".md") ? "page" : "folder";

  const [loading, setLoading] = useState(true);
  const [disabled, setDisabled] = useState(false); // effective ingestion-disable
  const [instruction, setInstruction] = useState(""); // this path's own (explicit)
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function applyResponse(r: UpdatePolicyResponse) {
    setDisabled(r.effective.ingestion_auto_update_disabled);
    setInstruction(r.explicit?.update_instruction ?? "");
  }

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    setEditing(false);
    getUpdatePolicy(path)
      .then((r) => {
        if (alive) applyResponse(r);
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

  // The toggle reads as "Auto-Update Wiki" — ON means ingestion auto-update is
  // enabled, i.e. NOT disabled. Saves instantly (settings-switch feel).
  async function onToggle(on: boolean) {
    const prev = disabled;
    setDisabled(!on);
    setSaving(true);
    setError(null);
    try {
      const r = await setUpdatePolicy(path, {
        ingestion_auto_update_disabled: !on,
        update_instruction: instruction || null,
      });
      applyResponse(r);
    } catch (e) {
      setDisabled(prev); // revert optimistic flip
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  async function saveInstruction() {
    setSaving(true);
    setError(null);
    try {
      const r = await setUpdatePolicy(path, {
        ingestion_auto_update_disabled: disabled,
        update_instruction: draft.trim() || null,
      });
      applyResponse(r);
      setEditing(false);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  }

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
              </div>
              <Switch
                checked={!disabled}
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
                    setDraft(instruction);
                    setEditing(true);
                  }}
                />
              )}
            </div>

            {!editing && instruction && (
              <div className={styles.instruction}>{instruction}</div>
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
                    onClick={() => void saveInstruction()}
                  >
                    {saving ? "Saving…" : "Save"}
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
        {error && <div className={styles.error}>{error}</div>}
      </div>
    </div>
  );
}
