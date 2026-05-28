"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/common/Button";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { apiFetch } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { color, radius } from "@/lib/theme";
import { useIsMobile } from "@/lib/viewport";

interface BraintrustSettings {
  project: string;
  api_key_set: boolean;
  api_key_hint: string;
  enabled: boolean;
}

export default function AdminBraintrustPage() {
  const { user, loading } = useRequireAuth();
  const router = useRouter();
  const isMobile = useIsMobile();

  useEffect(() => {
    if (!loading && user && !user.is_admin) router.replace("/");
  }, [loading, user, router]);

  if (loading || !user) return <main style={{ padding: isMobile ? 16 : 32 }}>Loading…</main>;
  if (!user.is_admin) return null;

  return (
    <main style={{ padding: isMobile ? "16px 12px" : "24px 32px", maxWidth: 720 }}>
        <BackLink />
        <PageHeader
          title="Braintrust tracing"
          description="Send LLM exchanges (messages, tools available, tool calls + results, model output, usage) to a Braintrust project for inspection. Tracing only fires when both project and API key are saved AND the toggle below is on."
        />
        <BraintrustForm />
    </main>
  );
}

function BraintrustForm() {
  const [settings, setSettings] = useState<BraintrustSettings | null>(null);
  const [project, setProject] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function load() {
    try {
      const r = await apiFetch<BraintrustSettings>("/admin/braintrust");
      setSettings(r);
      setProject(r.project);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!settings) return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const body: Record<string, unknown> = {
        project,
        enabled: settings.enabled,
      };
      if (apiKey) body.api_key = apiKey;
      const r = await apiFetch<BraintrustSettings>("/admin/braintrust", {
        method: "PUT",
        body: JSON.stringify(body),
      });
      setSettings(r);
      setApiKey("");
      setSaved("Saved.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    } finally {
      setSaving(false);
    }
  }

  async function clearKey() {
    if (!confirm("Clear the Braintrust API key? Tracing will be disabled until a new key is set.")) return;
    if (!settings) return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const r = await apiFetch<BraintrustSettings>("/admin/braintrust", {
        method: "PUT",
        body: JSON.stringify({ project, api_key: null, enabled: false }),
      });
      setSettings(r);
      setApiKey("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to clear");
    } finally {
      setSaving(false);
    }
  }

  async function toggleEnabled(next: boolean) {
    if (!settings) return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const r = await apiFetch<BraintrustSettings>("/admin/braintrust", {
        method: "PUT",
        body: JSON.stringify({ project: settings.project, enabled: next }),
      });
      setSettings(r);
      setSaved(next ? "Tracing enabled." : "Tracing disabled.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to update");
    } finally {
      setSaving(false);
    }
  }

  if (!settings) return <div>Loading…</div>;

  // Server-side: enabled is forced to false unless project AND key are both
  // set. Mirror that gating here so the toggle button is greyed out until
  // the user has saved both — clicking it before then would just have the
  // server reject.
  const canEnable = Boolean(settings.project) && settings.api_key_set;
  const projectChanged = project !== settings.project;
  const fieldsDirty = projectChanged || apiKey.length > 0;

  return (
    <form onSubmit={onSubmit} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <label>
        <div style={lblStyle}>Project</div>
        <input
          value={project}
          onChange={(e) => setProject(e.target.value)}
          placeholder="agent-wiki"
          required
          style={inputStyle}
        />
      </label>

      <KeyField
        label="Braintrust API key"
        value={apiKey}
        onChange={setApiKey}
        isSet={settings.api_key_set}
        hint={settings.api_key_hint}
        placeholder="sk-…"
        onClear={() => void clearKey()}
        clearDisabled={saving || !settings.api_key_set}
      />

      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 14px",
          border: `1px solid ${color.border.default}`,
          borderRadius: radius.sm,
          background: color.bg.sunken,
        }}
      >
        <div>
          <div style={{ fontSize: 13, fontWeight: 500 }}>
            Tracing is currently <strong>{settings.enabled ? "ON" : "OFF"}</strong>
          </div>
          <div style={{ fontSize: 12, color: color.text.muted, marginTop: 2 }}>
            {canEnable
              ? "Toggle sends every LLM call, tool call, and flow span to Braintrust."
              : "Save a project name and API key first to enable tracing."}
          </div>
        </div>
        <Button
          type="button"
          variant={settings.enabled ? "danger" : "primary"}
          size="sm"
          disabled={saving || !canEnable || fieldsDirty}
          onClick={() => void toggleEnabled(!settings.enabled)}
        >
          {settings.enabled ? "Disable" : "Enable"}
        </Button>
      </div>

      {error && <div style={{ color: color.state.danger.fg }}>{error}</div>}
      {saved && <div style={{ color: color.state.success.fg }}>{saved}</div>}
      <div>
        <Button type="submit" variant="primary" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </form>
  );
}

function KeyField({
  label,
  value,
  onChange,
  isSet,
  hint,
  placeholder,
  onClear,
  clearDisabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  isSet: boolean;
  hint: string;
  placeholder: string;
  onClear: () => void;
  clearDisabled: boolean;
}) {
  return (
    <label>
      <div style={{ ...lblStyle, display: "flex", alignItems: "center", gap: 6 }}>
        <span>{label}</span>
        {isSet && <span style={hintStyle}>currently {hint}</span>}
        <span style={{ flex: 1 }} />
        {isSet && (
          <Button
            type="button"
            size="sm"
            variant="danger"
            onClick={onClear}
            disabled={clearDisabled}
            style={{ padding: "2px 8px", fontSize: 12 }}
          >
            Clear
          </Button>
        )}
      </div>
      <input
        type="password"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={isSet ? "leave blank to keep" : placeholder}
        style={inputStyle}
      />
    </label>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  boxSizing: "border-box",
  border: `1px solid ${color.border.default}`,
  borderRadius: radius.sm,
  fontSize: 14,
};
const lblStyle: React.CSSProperties = { marginBottom: 4, fontSize: 13, fontWeight: 500 };
const hintStyle: React.CSSProperties = {
  fontWeight: 400,
  color: color.text.muted,
  fontFamily: "ui-monospace, monospace",
  fontSize: 12,
};
