"use client";

import { useEffect, useState, type CSSProperties, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/common/AppShell";
import { Button } from "@/components/common/Button";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { useRequireAuth } from "@/lib/auth";
import {
  createTemplate,
  deleteTemplate,
  updateTemplate,
  useAdminTemplates,
  type DocumentTemplate,
} from "@/lib/templates";
import { color, radius, shadow } from "@/lib/theme";
import { useIsMobile } from "@/lib/viewport";

export default function AdminTemplatesPage() {
  const { user, loading } = useRequireAuth();
  const router = useRouter();
  const isMobile = useIsMobile();

  useEffect(() => {
    if (!loading && user && !user.is_admin) router.replace("/");
  }, [loading, user, router]);

  if (loading || !user) return <main style={{ padding: isMobile ? 16 : 32 }}>Loading…</main>;
  if (!user.is_admin) return null;

  return (
    <AppShell>
      <main style={{ padding: isMobile ? "16px 12px" : "24px 32px", maxWidth: 960 }}>
        <BackLink />
        <PageHeader
          title="Document templates"
          description="Define named starting points users can pick when creating a new wiki page. Each template can supply an optional chat system prompt that guides the in-app assistant while the user is still drafting the initial version."
        />
        <TemplatesList />
      </main>
    </AppShell>
  );
}

function TemplatesList() {
  const { templates, error, isLoading, refresh } = useAdminTemplates();
  const [editing, setEditing] = useState<DocumentTemplate | "new" | null>(null);

  if (isLoading) return <div>Loading…</div>;
  if (error) return <div style={{ color: color.state.danger.fg }}>{error.message}</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <Button variant="primary" onClick={() => setEditing("new")}>
          New template
        </Button>
      </div>
      {templates.length === 0 ? (
        <div
          style={{
            padding: 24,
            border: `1px dashed ${color.border.default}`,
            borderRadius: radius.md,
            color: color.text.muted,
            textAlign: "center",
          }}
        >
          No templates yet. Click "New template" to define the first one.
        </div>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 8 }}>
          {templates.map((t) => (
            <li
              key={t.id}
              style={{
                padding: "12px 16px",
                border: `1px solid ${color.border.default}`,
                borderRadius: radius.md,
                background: color.bg.page,
                display: "flex",
                alignItems: "center",
                gap: 12,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 14 }}>{t.name}</div>
                {t.description && (
                  <div
                    style={{
                      fontSize: 13,
                      color: color.text.muted,
                      marginTop: 2,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {t.description}
                  </div>
                )}
                <div style={{ fontSize: 12, color: color.text.faint, marginTop: 4 }}>
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
      style={{
        position: "fixed",
        inset: 0,
        background: color.overlay,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
        padding: 16,
      }}
      onClick={onClose}
    >
      <form
        onSubmit={onSubmit}
        onClick={(e) => e.stopPropagation()}
        style={{
          background: color.bg.page,
          borderRadius: radius.lg,
          boxShadow: shadow.modal,
          padding: 20,
          width: "min(640px, 100%)",
          maxHeight: "90vh",
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <h2 style={{ margin: 0, fontSize: 18 }}>
          {initial ? "Edit template" : "New template"}
        </h2>

        <label>
          <div style={lblStyle}>Name *</div>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Project brief, RFC, Meeting notes"
            required
            style={inputStyle}
          />
        </label>

        <label>
          <div style={lblStyle}>Description</div>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional. Shown in the picker."
            style={inputStyle}
          />
        </label>

        <label>
          <div style={lblStyle}>Markdown body *</div>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="# Title&#10;&#10;## Section&#10;…"
            required
            rows={12}
            style={{ ...inputStyle, fontFamily: "ui-monospace, monospace", resize: "vertical" }}
          />
        </label>

        <label>
          <div style={lblStyle}>Chat system prompt</div>
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="Optional. Appended to the chat agent's default prompt while the user is drafting from this template."
            rows={5}
            style={{ ...inputStyle, fontFamily: "ui-monospace, monospace", resize: "vertical" }}
          />
        </label>

        {error && <div style={{ color: color.state.danger.fg, fontSize: 13 }}>{error}</div>}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
          <Button type="button" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={saving}>
            {saving ? "Saving…" : initial ? "Save" : "Create"}
          </Button>
        </div>
      </form>
    </div>
  );
}

const inputStyle: CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  boxSizing: "border-box",
  border: `1px solid ${color.border.default}`,
  borderRadius: radius.sm,
  fontSize: 14,
};
const lblStyle: CSSProperties = { marginBottom: 4, fontSize: 13, fontWeight: 500 };
