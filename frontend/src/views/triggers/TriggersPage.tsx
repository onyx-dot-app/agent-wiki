"use client";

import { useState } from "react";

import useSWR from "swr";

import { Button, Tabs, Text } from "@onyx-ai/opal/components";
import { SvgPlusCircle, SvgSearch, SvgWorkflow } from "@onyx-ai/opal/icons";
import { SettingsLayouts } from "@onyx-ai/opal/layouts";
import { useConfirm } from "@/components/common/ConfirmDialog";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { TriggerHistoryModal } from "@/components/triggers/TriggerHistoryModal";
import { TriggerPanel } from "@/components/triggers/TriggerPanel";
import { useRequireAuth } from "@/lib/auth";
import { TriggerCard } from "@/components/triggers/TriggerCard";
import {
  deleteTrigger,
  getTriggerFires,
  getTriggerVersion,
  updateTrigger,
  useDestinationConfigs,
  useTriggers,
  type Trigger,
  type TriggerFire,
} from "@/lib/triggers";

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const diffMs = Date.now() - t;
  const sec = Math.round(diffMs / 1000);
  if (sec < 60) return "just now";
  const min = Math.round(sec / 60);
  if (min < 60) return `${min} min ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} hr ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day} day${day === 1 ? "" : "s"} ago`;
  return new Date(iso).toLocaleDateString();
}

export default function TriggersPage() {
  const { user, loading } = useRequireAuth();
  const { triggers, error: listSwrError, refresh } = useTriggers();
  const { configs } = useDestinationConfigs();
  const [kindTab, setKindTab] = useState<"delta" | "schedule">("delta");
  const [search, setSearch] = useState("");
  const { data: firesData } = useSWR("/triggers/fires?per_trigger=3", () =>
    getTriggerFires({ perTrigger: 3 }),
  );
  const firesByTrigger = new Map<string, TriggerFire[]>();
  for (const f of firesData ?? []) {
    const cur = firesByTrigger.get(f.trigger_id);
    if (cur) cur.push(f);
    else firesByTrigger.set(f.trigger_id, [f]);
  }
  const q = search.trim().toLowerCase();
  const visible = triggers.filter(
    (t) =>
      t.kind === kindTab &&
      (!q ||
        t.scope_path.toLowerCase().includes(q) ||
        t.nl_description.toLowerCase().includes(q) ||
        t.actions.some((a) => (a.message ?? "").toLowerCase().includes(q))),
  );
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Trigger | null>(null);
  const [historyFor, setHistoryFor] = useState<Trigger | null>(null);
  const confirmDialog = useConfirm();

  const listError = mutationError ?? listSwrError?.message ?? null;

  if (loading || !user) return <LoadingSpinner center />;

  async function onToggle(t: Trigger) {
    setBusyId(t.id);
    setMutationError(null);
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
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "toggle failed");
    } finally {
      setBusyId(null);
    }
  }

  async function onDelete(t: Trigger) {
    if (
      !(await confirmDialog({
        title: "Delete this trigger?",
        body: `"${t.nl_description}"`,
        confirmLabel: "Delete",
      }))
    )
      return;
    setBusyId(t.id);
    setMutationError(null);
    try {
      await deleteTrigger(t.id);
      await refresh(
        (cur) => ({
          triggers: (cur?.triggers ?? []).filter((x) => x.id !== t.id),
        }),
        { revalidate: true },
      );
    } catch (e) {
      setMutationError(e instanceof Error ? e.message : "delete failed");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <SettingsLayouts.Root width="lg">
      <SettingsLayouts.Header
        icon={SvgWorkflow}
        title="Triggers"
        description="Watch wiki pages for specific changes, or check on recurring schedules."
        divider
      />
      <SettingsLayouts.Body>
        <Tabs
          value={kindTab}
          onValueChange={(v) => setKindTab(v as "delta" | "schedule")}
          variant="contained"
        >
          <Tabs.List>
            <Tabs.Trigger value="delta">Run on Wiki Updates</Tabs.Trigger>
            <Tabs.Trigger value="schedule">Recurring Schedule</Tabs.Trigger>
          </Tabs.List>
        </Tabs>
        <div className="flex w-full items-center gap-3 px-2 py-3">
          <div className="flex min-w-[160px] flex-1 items-center gap-1 rounded-(--radius-08) p-[6px] text-[14px] leading-5">
            <span className="flex size-6 items-center justify-center p-1">
              <SvgSearch className="size-4 text-(--text-03)" />
            </span>
            {/* raw-ok: bare .opal-input-field; the borderless search bar composite has no Opal component */}
            <input
              className="opal-input-field min-w-0 flex-1"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search triggers…"
            />
          </div>
          <Button
            variant="action"
            rightIcon={SvgPlusCircle}
            onClick={() => {
              setEditing(null);
              setModalOpen(true);
            }}
          >
            Add Trigger
          </Button>
        </div>
        {listError && (
          <div className="mb-3 rounded-(--radius-04) bg-(--status-error-01) p-[10px] text-[13px] text-(--status-text-error-05)">
            {listError}
          </div>
        )}

        {visible.length === 0 && !listError && (
          <div className="px-2 py-4">
            <Text font="main-ui-body" color="text-03">
              {q
                ? "No triggers match the search."
                : kindTab === "delta"
                  ? "No update triggers yet. Add one to start watching pages for changes."
                  : "No scheduled triggers yet. Add one to run checks on a recurring schedule."}
            </Text>
          </div>
        )}

        <div className="flex w-full flex-col gap-2">
          {visible.map((t) => (
            <TriggerCard
              key={t.id}
              trigger={t}
              fires={firesByTrigger.get(t.id) ?? []}
              configs={configs}
              ownerName={user.name || user.email}
              busy={busyId === t.id}
              formatRelative={formatRelative}
              onToggle={() => void onToggle(t)}
              onEdit={() => {
                setEditing(t);
                setModalOpen(true);
              }}
              onHistory={() => setHistoryFor(t)}
            />
          ))}
        </div>
      </SettingsLayouts.Body>

      <TriggerPanel
        open={modalOpen}
        initial={editing ?? undefined}
        onClose={() => {
          setModalOpen(false);
          setEditing(null);
        }}
        onSaved={(saved) => {
          void refresh(
            (cur) => {
              const prev = cur?.triggers ?? [];
              const i = prev.findIndex((t) => t.id === saved.id);
              if (i === -1) return { triggers: [saved, ...prev] };
              const next = prev.slice();
              next[i] = saved;
              return { triggers: next };
            },
            { revalidate: true },
          );
        }}
      />

      <TriggerHistoryModal
        trigger={historyFor}
        onClose={() => setHistoryFor(null)}
        onSelectVersion={async (sha) => {
          if (!historyFor) return;
          try {
            const version = await getTriggerVersion(historyFor.id, sha);
            setEditing({
              ...historyFor,
              scope_path: version.scope_path,
              nl_description: version.nl_description,
              actions: version.actions,
              enabled: version.enabled,
              kind: version.kind ?? historyFor.kind,
              schedule_cron: version.schedule_cron,
              schedule_timezone: version.schedule_timezone,
              schedule_start_at: version.schedule_start_at,
            });
            setHistoryFor(null);
            setModalOpen(true);
          } catch (e) {
            setMutationError(
              e instanceof Error ? e.message : "failed to load version",
            );
          }
        }}
      />
    </SettingsLayouts.Root>
  );
}
