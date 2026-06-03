"use client";

import { useEffect, useState } from "react";

import { Button } from "@onyx-ai/opal/components";
import { SvgX } from "@onyx-ai/opal/icons";
import { formatScopePath } from "@/lib/format";
import {
  getTriggerHistory,
  type Trigger,
  type TriggerCommit,
} from "@/lib/triggers";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";

interface Props {
  trigger: Trigger | null;
  onClose: () => void;
  onSelectVersion: (sha: string) => void;
}

function formatTs(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function TriggerHistoryModal({
  trigger,
  onClose,
  onSelectVersion,
}: Props) {
  const [commits, setCommits] = useState<TriggerCommit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!trigger) {
      setCommits([]);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    getTriggerHistory(trigger.id)
      .then((rows) => setCommits(rows))
      .catch((e) =>
        setError(e instanceof Error ? e.message : "failed to load history"),
      )
      .finally(() => setLoading(false));
  }, [trigger]);

  if (!trigger) return null;

  return (
    <div
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      className="fixed inset-0 z-[100] flex items-center justify-center bg-(--mask-03)"
    >
      <div className="flex max-h-[92vh] w-[min(640px,92vw)] flex-col gap-[14px] overflow-y-auto rounded-(--border-radius-12) bg-(--background-tint-00) p-6 shadow-(--shadow-modal)">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="m-0 text-[18px] font-bold">Edit history</h2>
            <div
              title={trigger.scope_path}
              className="mt-1 font-mono text-xs text-(--text-03)"
            >
              {formatScopePath(trigger.scope_path)}
            </div>
          </div>
          <Button
            icon={SvgX}
            prominence="tertiary"
            size="sm"
            tooltip="Close"
            onClick={onClose}
          />
        </div>

        <p className="m-0 text-xs leading-[1.5] text-(--text-03)">
          Click a version to open it in the editor. Saving from there creates a
          new commit. Trigger <em>fires</em> live on the Events tab.
        </p>

        {loading && <LoadingSpinner />}

        {error && (
          <div className="rounded-(--border-radius-04) bg-(--status-error-01) p-[10px] text-[13px] text-(--status-text-error-05)">
            {error}
          </div>
        )}

        {!loading && !error && commits.length === 0 && (
          <div className="text-[13px] text-(--text-03)">No history yet.</div>
        )}

        {commits.length > 0 && (
          <ul className="m-0 list-none p-0">
            {commits.map((c) => (
              <li key={c.sha} className="mb-[6px]">
                <button
                  type="button"
                  onClick={() => onSelectVersion(c.sha)}
                  className="flex w-full cursor-pointer items-baseline justify-between gap-3 rounded-(--border-radius-08) border border-(--border-01) bg-(--background-tint-01) px-3 py-[10px] text-left font-[inherit]"
                >
                  <span className="text-[13px] text-(--text-05)">
                    {formatTs(c.ts)}
                  </span>
                  <span className="text-xs text-(--text-03)">{c.author}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
