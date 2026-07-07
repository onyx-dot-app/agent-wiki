"use client";

import { useState } from "react";
import useSWR from "swr";

import { Button, LinkButton, InputTypeIn } from "@onyx-ai/opal/components";
import { SvgExternalLink, SvgPlusCircle, SvgX } from "@onyx-ai/opal/icons";
import { IllustrationContent } from "@onyx-ai/opal/layouts";
import { SvgEmpty } from "@onyx-ai/opal/illustrations";

import { TriggerCard } from "@/components/triggers/TriggerCard";
import { useAuth } from "@/lib/auth";
import { formatRelative } from "@/lib/format";
import {
  getTriggerFires,
  updateTrigger,
  useDestinationConfigs,
  useTriggers,
  type Trigger,
  type TriggerFire,
} from "@/lib/triggers";

/** Faithful mirror of the backend's ``_in_scope`` (app/triggers/diff.py):
 * empty scope = whole wiki, else exact match or directory prefix. Stored
 * scopes arrive normalized (no leading slash; root collapsed to ""). */
function scopeCovers(scope: string, path: string): boolean {
  if (!scope) return true;
  if (path === scope) return true;
  return path.startsWith(`${scope.replace(/\/+$/, "")}/`);
}

interface Props {
  path: string;
  onClose?: () => void;
  onEdit: (trigger: Trigger) => void;
  onAdd: () => void;
}

/** The doc page's automations rail: this page's triggers as cards, with
 * search and a link out to the full Triggers page. */
export function AutomationsPanel({ path, onClose, onEdit, onAdd }: Props) {
  const { user } = useAuth();
  const { triggers, refresh } = useTriggers();
  const { configs } = useDestinationConfigs();
  const { data: firesData } = useSWR("/triggers/fires?per_trigger=3", () =>
    getTriggerFires({ perTrigger: 3 }),
  );
  const [search, setSearch] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const firesByTrigger = new Map<string, TriggerFire[]>();
  for (const f of firesData ?? []) {
    const cur = firesByTrigger.get(f.trigger_id);
    if (cur) cur.push(f);
    else firesByTrigger.set(f.trigger_id, [f]);
  }

  const q = search.trim().toLowerCase();
  const visible = triggers.filter(
    (t) =>
      scopeCovers(t.scope_path, path) &&
      (!q ||
        t.nl_description.toLowerCase().includes(q) ||
        t.actions.some((a) => (a.message ?? "").toLowerCase().includes(q))),
  );

  async function onToggle(t: Trigger) {
    setBusyId(t.id);
    try {
      const updated = await updateTrigger(t.id, { enabled: !t.enabled });
      await refresh(
        (cur) => ({
          triggers: (cur?.triggers ?? []).map((x) =>
            x.id === t.id ? updated : x,
          ),
        }),
        { revalidate: true },
      );
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="flex h-full w-full flex-col gap-1 bg-(--background-tint-01) p-1">
      <div className="flex w-full items-center gap-1 rounded-(--radius-12) border border-(--border-01) bg-(--background-tint-00) p-[6px] [&>*:first-child]:min-w-0 [&>*:first-child]:flex-1">
        <InputTypeIn
          variant="internal"
          searchIcon
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search triggers…"
        />
        {onClose && (
          <Button
            type="button"
            icon={SvgX}
            size="sm"
            prominence="tertiary"
            tooltip="Close"
            onClick={onClose}
          />
        )}
      </div>

      <div className="flex min-h-0 w-full flex-1 flex-col gap-2 overflow-y-auto py-1">
        {visible.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-3">
            <IllustrationContent
              illustration={SvgEmpty}
              title={q ? "No matching triggers." : "No triggers on this page."}
            />
            {!q && (
              <Button
                type="button"
                variant="action"
                rightIcon={SvgPlusCircle}
                onClick={onAdd}
              >
                Add Trigger
              </Button>
            )}
          </div>
        )}
        {visible.map((t) => (
          <TriggerCard
            conditionsOnly
            key={t.id}
            trigger={t}
            fires={firesByTrigger.get(t.id) ?? []}
            configs={configs}
            ownerName={user?.name || user?.email || "?"}
            busy={busyId === t.id}
            formatRelative={formatRelative}
            onToggle={() => void onToggle(t)}
            onEdit={() => onEdit(t)}
          />
        ))}
      </div>

      <div className="flex w-full items-center justify-between p-2">
        <span className="flex items-center gap-1">
          <SvgExternalLink className="size-4 text-(--text-03)" />
          <LinkButton href="/app/triggers" target="_self">
            All Triggers
          </LinkButton>
        </span>
        <Button
          type="button"
          size="sm"
          variant="action"
          rightIcon={SvgPlusCircle}
          onClick={onAdd}
        >
          Add Trigger
        </Button>
      </div>
    </div>
  );
}
