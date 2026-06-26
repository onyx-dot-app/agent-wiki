"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@onyx-ai/opal/components";
import { SvgChevronDown, SvgChevronUp } from "@onyx-ai/opal/icons";
import { useConfirm } from "@/components/common/ConfirmDialog";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import {
  createTemplate,
  deleteTemplate,
  reorderTemplates,
  updateTemplate,
  useAdminTemplates,
  type DocumentTemplate,
} from "@/lib/templates";
import { useIsMobile } from "@/lib/viewport";

export default function AdminTemplatesPage() {
  const isMobile = useIsMobile();
  return (
    <RequireAdmin>
      <main className={`max-w-[960px] ${isMobile ? "px-3 py-4" : "px-8 py-6"}`}>
        <BackLink />
        <PageHeader
          title="Document templates"
          description="Define named starting points users can pick when creating a new wiki page. Each template can supply an optional chat system prompt that guides the in-app assistant while the user is still drafting the initial version."
        />
        <TemplatesList />
      </main>
    </RequireAdmin>
  );
}

function TemplatesList() {
  const { templates, error, isLoading, refresh } = useAdminTemplates();
  const [editing, setEditing] = useState<DocumentTemplate | "new" | null>(null);
  // Tracks the id whose row is mid-reorder so we can disable its arrow
  // buttons. A single in-flight request at a time keeps the optimistic
  // ordering and the server's view in sync without manual reconciliation.
  const [reordering, setReordering] = useState<string | null>(null);
  const [reorderError, setReorderError] = useState<string | null>(null);
  const confirmDialog = useConfirm();

  if (isLoading) return <LoadingSpinner />;
  if (error)
    return <div className="text-(--status-text-error-05)">{error.message}</div>;

  async function move(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= templates.length) return;
    const next = [...templates];
    const [moved] = next.splice(index, 1);
    next.splice(target, 0, moved);
    const ids = next.map((t) => t.id);
    // Optimistic update so the row jumps immediately; SWR returns the
    // authoritative list on success.
    setReordering(moved.id);
    setReorderError(null);
    void refresh(
      async () => {
        try {
          return await reorderTemplates(ids);
        } catch (e) {
          setReorderError(e instanceof Error ? e.message : "reorder failed");
          throw e;
        } finally {
          setReordering(null);
        }
      },
      {
        optimisticData: { templates: next },
        rollbackOnError: true,
        revalidate: false,
      },
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">
        <Button variant="action" onClick={() => setEditing("new")}>
          New template
        </Button>
      </div>
      {reorderError && (
        <div className="rounded-(--border-radius-04) border border-(--status-error-02) bg-(--status-error-01) px-3 py-2 text-[13px] text-(--status-text-error-05)">
          {reorderError}
        </div>
      )}
      {templates.length === 0 ? (
        <div className="rounded-(--border-radius-08) border border-dashed border-(--border-01) p-6 text-center text-(--text-03)">
          No templates yet. Click "New template" to define the first one.
        </div>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {templates.map((t, i) => (
            <li
              key={t.id}
              className="flex items-center gap-3 rounded-(--border-radius-08) border border-(--border-01) bg-(--background-tint-00) px-4 py-3"
            >
              <ReorderHandle
                disabled={reordering !== null}
                canUp={i > 0}
                canDown={i < templates.length - 1}
                onUp={() => void move(i, -1)}
                onDown={() => void move(i, 1)}
              />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-semibold">{t.name}</div>
                {t.description && (
                  <div className="mt-[2px] overflow-hidden text-[13px] text-ellipsis whitespace-nowrap text-(--text-03)">
                    {t.description}
                  </div>
                )}
                <div className="mt-1 text-xs text-(--text-02)">
                  {t.ingestion_auto_update_disabled == null
                    ? "Auto-update: default"
                    : t.ingestion_auto_update_disabled
                      ? "Auto-update: off"
                      : "Auto-update: on"}{" "}
                  • {t.system_prompt ? "Has chat prompt" : "No chat prompt"} •
                  Updated {t.updated_at}
                </div>
              </div>
              <Button size="sm" onClick={() => setEditing(t)}>
                Edit
              </Button>
              <Button
                size="sm"
                variant="danger"
                onClick={async () => {
                  if (
                    !(await confirmDialog({
                      title: `Delete template "${t.name}"?`,
                      confirmLabel: "Delete",
                    }))
                  )
                    return;
                  await deleteTemplate(t.id);
                  await refresh();
                }}
              >
                Delete
              </Button>
            </li>
          ))}
        </ul>
      )}
      {editing && (
        <TemplateModal
          initial={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await refresh();
          }}
        />
      )}
    </div>
  );
}

function ReorderHandle({
  canUp,
  canDown,
  disabled,
  onUp,
  onDown,
}: {
  canUp: boolean;
  canDown: boolean;
  disabled: boolean;
  onUp: () => void;
  onDown: () => void;
}) {
  return (
    <div
      className="flex shrink-0 flex-col gap-[2px]"
      aria-label="Reorder template"
    >
      <ArrowButton
        title="Move up"
        disabled={disabled || !canUp}
        onClick={onUp}
        direction="up"
      />
      <ArrowButton
        title="Move down"
        disabled={disabled || !canDown}
        onClick={onDown}
        direction="down"
      />
    </div>
  );
}

function ArrowButton({
  direction,
  title,
  disabled,
  onClick,
}: {
  direction: "up" | "down";
  title: string;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={title}
      className={`flex h-[18px] w-[24px] items-center justify-center rounded-(--border-radius-04) border border-(--border-01) bg-transparent p-0 text-(--text-04) ${disabled ? "cursor-not-allowed opacity-35" : "cursor-pointer"}`}
    >
      {direction === "up" ? (
        <SvgChevronUp size={10} />
      ) : (
        <SvgChevronDown size={10} />
      )}
    </button>
  );
}

function TemplateModal({
  initial,
  onClose,
  onSaved,
}: {
  initial: DocumentTemplate | null;
  onClose: () => void;
  onSaved: () => void | Promise<void>;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [body, setBody] = useState(initial?.body ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [systemPrompt, setSystemPrompt] = useState(
    initial?.system_prompt ?? "",
  );
  // Default auto-update for pages created from this template: "default" leaves
  // it to the workspace default; "on"/"off" set it explicitly.
  const [autoUpdate, setAutoUpdate] = useState<"default" | "on" | "off">(
    initial == null || initial.ingestion_auto_update_disabled == null
      ? "default"
      : initial.ingestion_auto_update_disabled
        ? "off"
        : "on",
  );
  const [updateInstruction, setUpdateInstruction] = useState(
    initial?.update_instruction ?? "",
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    if (!name.trim()) {
      setError("Name is required");
      return;
    }
    if (!body.trim()) {
      setError("Body (markdown) is required");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: name.trim(),
        body,
        description: description.trim() || null,
        system_prompt: systemPrompt.trim() || null,
        ingestion_auto_update_disabled:
          autoUpdate === "default" ? null : autoUpdate === "off",
        update_instruction: updateInstruction.trim() || null,
      };
      if (initial) {
        await updateTemplate(initial.id, payload);
      } else {
        await createTemplate(payload);
      }
      await onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[1000] flex items-center justify-center bg-(--mask-03) p-4"
      onClick={onClose}
    >
      <form
        onSubmit={onSubmit}
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[90vh] w-[min(640px,100%)] flex-col gap-3 overflow-y-auto rounded-(--border-radius-12) bg-(--background-tint-00) p-5 shadow-(--shadow-modal)"
      >
        <h2 className="m-0 text-lg">
          {initial ? "Edit template" : "New template"}
        </h2>

        <label>
          <div className={lblClass}>Name *</div>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Project brief, RFC, Meeting notes"
            required
            className={inputClass}
          />
        </label>

        <label>
          <div className={lblClass}>Description</div>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional. Shown in the picker."
            className={inputClass}
          />
        </label>

        <label>
          <div className={lblClass}>Markdown body *</div>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="# Title&#10;&#10;## Section&#10;…"
            required
            rows={12}
            className={`${inputClass} resize-y font-mono`}
          />
        </label>

        <label>
          <div className={lblClass}>Chat system prompt</div>
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="Optional. Appended to the chat agent's default prompt while the user is drafting from this template."
            rows={5}
            className={`${inputClass} resize-y font-mono`}
          />
        </label>

        <label>
          <div className={lblClass}>Auto-update default</div>
          <select
            value={autoUpdate}
            onChange={(e) =>
              setAutoUpdate(e.target.value as "default" | "on" | "off")
            }
            className={inputClass}
          >
            <option value="default">Use workspace default</option>
            <option value="on">On — keep pages current from ingestion</option>
            <option value="off">
              Off — pages start with auto-update disabled
            </option>
          </select>
          <div className="mt-1 text-xs text-(--text-02)">
            Applied to a page when it's created from this template. e.g. set
            "off" for meeting notes that shouldn't be rewritten by ingestion.
          </div>
        </label>

        <label>
          <div className={lblClass}>Update instructions</div>
          <textarea
            value={updateInstruction}
            onChange={(e) => setUpdateInstruction(e.target.value)}
            placeholder="Optional. Scope/how-to guidance for the updater, seeded onto pages made from this template (e.g. 'Only track decisions and owners; ignore status chatter')."
            rows={4}
            className={`${inputClass} resize-y`}
          />
        </label>

        {error && (
          <div className="text-[13px] text-(--status-text-error-05)">
            {error}
          </div>
        )}

        <div className="mt-1 flex justify-end gap-2">
          <Button type="button" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" variant="action" disabled={saving}>
            {saving ? "Saving…" : initial ? "Save" : "Create"}
          </Button>
        </div>
      </form>
    </div>
  );
}

const inputClass =
  "w-full py-2 px-[10px] box-border border border-(--border-01) rounded-(--border-radius-04) text-sm";
const lblClass = "mb-1 text-[13px] font-medium";
