"use client";

import { useState } from "react";

import { Button, Card, Divider } from "@onyx-ai/opal/components";
import { SvgExpand, SvgFold } from "@onyx-ai/opal/icons";

import { ActivityRow } from "@/components/wiki/ActivitiesPanel";
import { AutomationsPanel } from "@/components/wiki/AutomationsPanel";
import { TriggerPanel } from "@/components/triggers/TriggerPanel";
import { useConfirm } from "@/components/common/ConfirmDialog";
import { useAuth } from "@/lib/auth";
import { useEvents } from "@/lib/activities";
import { deleteTrigger, useTriggers, type Trigger } from "@/lib/triggers";

/** One accordion section row per the mock: a bordered card holding a titled
 * Divider with the 24px expand icon button at its right edge. */
function SectionRow({
  title,
  open,
  onToggle,
  children,
}: {
  title: string;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <Card padding="xs" expandable expanded={open} expandedContent={children}>
      <div className="flex w-full items-center gap-1">
        <div className="min-w-0 flex-1">
          <Divider title={title} />
        </div>
        <Button
          type="button"
          icon={open ? SvgFold : SvgExpand}
          size="sm"
          prominence="internal"
          tooltip={open ? "Collapse" : "Expand"}
          onClick={onToggle}
        />
      </div>
    </Card>
  );
}

/** The doc page's right-panel accordion per the mock: the New Trigger form
 * (expanded by default, header X replaced by a collapse control), then
 * Activity History and Triggers sections scoped to this page. Sections
 * expand and collapse independently; the column scrolls. */
export function TriggersSidePanel({
  path,
  onStatus,
}: {
  path: string;
  onStatus: (message: string) => void;
}) {
  const [formOpen, setFormOpen] = useState(true);
  const [activityOpen, setActivityOpen] = useState(false);
  const [triggersOpen, setTriggersOpen] = useState(false);
  const [editingTrigger, setEditingTrigger] = useState<Trigger | null>(null);
  const confirmDialog = useConfirm();
  const { refresh: refreshTriggers } = useTriggers();
  const { user } = useAuth();
  const ownerName = user?.name || user?.email || "?";
  const { events } = useEvents({ limit: 100 }, { refreshInterval: 30_000 });

  const docEvents = events.filter(
    (ev) =>
      (ev.payload as { doc_path?: string }).doc_path === path ||
      ev.target === path,
  );

  function openForm(trigger: Trigger | null) {
    setEditingTrigger(trigger);
    setFormOpen(true);
  }

  return (
    <div className="flex h-full w-full flex-col gap-2 overflow-y-auto">
      {formOpen ? (
        <TriggerPanel
          open
          docked
          initial={editingTrigger ?? { scope_path: path }}
          lockScope={!editingTrigger}
          onDelete={
            editingTrigger
              ? async () => {
                  if (
                    !(await confirmDialog({
                      title: "Delete this trigger?",
                      body: `"${editingTrigger.nl_description}"`,
                      confirmLabel: "Delete",
                    }))
                  )
                    return;
                  await deleteTrigger(editingTrigger.id);
                  await refreshTriggers();
                  setEditingTrigger(null);
                  setFormOpen(false);
                }
              : undefined
          }
          onClose={() => {
            setFormOpen(false);
            setEditingTrigger(null);
          }}
          onSaved={(t) => {
            onStatus(
              editingTrigger
                ? `Updated trigger for ${t.scope_path}`
                : `Created trigger for ${t.scope_path}`,
            );
            void refreshTriggers();
          }}
        />
      ) : (
        <SectionRow
          title={editingTrigger ? "Edit Trigger" : "New Trigger"}
          open={false}
          onToggle={() => setFormOpen(true)}
        >
          {null}
        </SectionRow>
      )}

      <SectionRow
        title="Activity History"
        open={activityOpen}
        onToggle={() => setActivityOpen((v) => !v)}
      >
        <div className="flex max-h-[320px] w-full flex-col overflow-y-auto p-1">
          {docEvents.length === 0 && (
            <div className="p-2 text-center text-[12px] leading-4 text-(--text-03)">
              No trigger activity for this page yet.
            </div>
          )}
          {docEvents.map((ev) => (
            <ActivityRow key={ev.id} event={ev} ownerName={ownerName} />
          ))}
        </div>
      </SectionRow>

      <SectionRow
        title="Triggers"
        open={triggersOpen}
        onToggle={() => setTriggersOpen((v) => !v)}
      >
        <div className="h-[360px] w-full">
          <AutomationsPanel
            path={path}
            onEdit={(t) => openForm(t)}
            onAdd={() => openForm(null)}
          />
        </div>
      </SectionRow>
    </div>
  );
}
