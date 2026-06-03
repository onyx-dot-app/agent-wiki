"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@onyx-ai/opal/components";
import { SvgChevronDown, SvgChevronUp } from "@onyx-ai/opal/icons";
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
      <main className={`max-w-[960px] ${isMobile ? "py-4 px-3" : "py-6 px-8"}`}>
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

  if (isLoading) return <LoadingSpinner />;
  if (error) return <div className="text-(--color-state-danger-fg)">{error.message}</div>;

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
        <div className="py-2 px-3 bg-(--color-state-danger-bg) border border-(--color-state-danger-border) text-(--color-state-danger-fg) rounded-(--radius-sm) text-[13px]">
          {reorderError}
        </div>
      )}
      {templates.length === 0 ? (
        <div className="p-6 border border-dashed border-(--color-border-default) rounded-(--radius-md) text-(--color-text-muted) text-center">
          No templates yet. Click "New template" to define the first one.
        </div>
      ) : (
        <ul className="list-none p-0 m-0 flex flex-col gap-2">
          {templates.map((t, i) => (
            <li
              key={t.id}
              className="py-3 px-4 border border-(--color-border-default) rounded-(--radius-md) bg-(--color-bg-page) flex items-center gap-3"
            >
              <ReorderHandle
                disabled={reordering !== null}
                canUp={i > 0}
                canDown={i < templates.length - 1}
                onUp={() => void move(i, -1)}
                onDown={() => void move(i, 1)}
              />
              <div className="flex-1 min-w-0">
                <div className="font-semibold text-sm">{t.name}</div>
                {t.description && (
                  <div className="text-[13px] text-(--color-text-muted) mt-[2px] overflow-hidden text-ellipsis whitespace-nowrap">
                    {t.description}
                  </div>
                )}
                <div className="text-xs text-(--color-text-faint) mt-1">
                  {t.system_prompt ? "Has chat prompt" : "No chat prompt"} • Updated {t.updated_at}
                </div>
              </div>
              <Button size="sm" onClick={() => setEditing(t)}>
                Edit
              </Button>
              <Button
                size="sm"
                variant="danger"
                onClick={async () => {
                  if (!confirm(`Delete template "${t.name}"?`)) return;
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
      className="flex flex-col gap-[2px] shrink-0"
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
      className={`w-[24px] h-[18px] flex items-center justify-center bg-transparent border border-(--color-border-default) rounded-(--radius-xs) text-(--color-text-secondary) p-0 ${disabled ? "cursor-not-allowed opacity-35" : "cursor-pointer"}`}
    >
      {direction === "up" ? <SvgChevronUp size={10} /> : <SvgChevronDown size={10} />}
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
  const [systemPrompt, setSystemPrompt] = useState(initial?.system_prompt ?? "");
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
      className="fixed inset-0 bg-(--color-overlay) flex items-center justify-center z-[1000] p-4"
      onClick={onClose}
    >
      <form
        onSubmit={onSubmit}
        onClick={(e) => e.stopPropagation()}
        className="bg-(--color-bg-page) rounded-(--radius-lg) shadow-(--shadow-modal) p-5 w-[min(640px,100%)] max-h-[90vh] overflow-y-auto flex flex-col gap-3"
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
            className={`${inputClass} font-mono resize-y`}
          />
        </label>

        <label>
          <div className={lblClass}>Chat system prompt</div>
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="Optional. Appended to the chat agent's default prompt while the user is drafting from this template."
            rows={5}
            className={`${inputClass} font-mono resize-y`}
          />
        </label>

        {error && <div className="text-(--color-state-danger-fg) text-[13px]">{error}</div>}

        <div className="flex justify-end gap-2 mt-1">
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

const inputClass = "w-full py-2 px-[10px] box-border border border-(--color-border-default) rounded-(--radius-sm) text-sm";
const lblClass = "mb-1 text-[13px] font-medium";
