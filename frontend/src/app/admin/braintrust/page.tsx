"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@onyx-ai/opal/components";
import { useConfirm } from "@/components/common/ConfirmDialog";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { apiFetch } from "@/lib/api";
import { useIsMobile } from "@/lib/viewport";

interface BraintrustSettings {
  project: string;
  api_key_set: boolean;
  api_key_hint: string;
  enabled: boolean;
}

export default function AdminBraintrustPage() {
  const isMobile = useIsMobile();
  return (
    <RequireAdmin>
      <main
        className="max-w-[720px]"
        style={{ padding: isMobile ? "16px 12px" : "24px 32px" }}
      >
        <BackLink />
        <PageHeader
          title="Braintrust tracing"
          description="Send LLM exchanges (messages, tools available, tool calls + results, model output, usage) to a Braintrust project for inspection. Tracing only fires when both project and API key are saved AND the toggle below is on."
        />
        <BraintrustForm />
      </main>
    </RequireAdmin>
  );
}

function BraintrustForm() {
  const [settings, setSettings] = useState<BraintrustSettings | null>(null);
  const [project, setProject] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const confirmDialog = useConfirm();

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
    if (
      !(await confirmDialog({
        title: "Clear the Braintrust API key?",
        body: "Tracing will be disabled until a new key is set.",
        confirmLabel: "Clear key",
      }))
    )
      return;
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

  if (!settings) return <LoadingSpinner />;

  // Server-side: enabled is forced to false unless project AND key are both
  // set. Mirror that gating here so the toggle button is greyed out until
  // the user has saved both — clicking it before then would just have the
  // server reject.
  const canEnable = Boolean(settings.project) && settings.api_key_set;
  const projectChanged = project !== settings.project;
  const fieldsDirty = projectChanged || apiKey.length > 0;

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <label>
        <div className={lblClass}>Project</div>
        <input
          value={project}
          onChange={(e) => setProject(e.target.value)}
          placeholder="agent-wiki"
          required
          className={inputClass}
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

      <div className="flex items-center justify-between rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-02) px-[14px] py-3">
        <div>
          <div className="text-[13px] font-medium">
            Tracing is currently{" "}
            <strong>{settings.enabled ? "ON" : "OFF"}</strong>
          </div>
          <div className="mt-[2px] text-xs text-(--text-03)">
            {canEnable
              ? "Toggle sends every LLM call, tool call, and flow span to Braintrust."
              : "Save a project name and API key first to enable tracing."}
          </div>
        </div>
        <Button
          type="button"
          variant={settings.enabled ? "danger" : "action"}
          size="sm"
          disabled={saving || !canEnable || fieldsDirty}
          onClick={() => void toggleEnabled(!settings.enabled)}
        >
          {settings.enabled ? "Disable" : "Enable"}
        </Button>
      </div>

      {error && <div className="text-(--status-text-error-05)">{error}</div>}
      {saved && <div className="text-(--status-text-success-05)">{saved}</div>}
      <div>
        <Button type="submit" variant="action" disabled={saving}>
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
      <div className="mb-1 flex items-center gap-[6px] text-[13px] font-medium">
        <span>{label}</span>
        {isSet && (
          <span className="font-mono text-xs font-normal text-(--text-03)">
            currently {hint}
          </span>
        )}
        <span className="flex-1" />
        {isSet && (
          <Button
            type="button"
            size="sm"
            variant="danger"
            onClick={onClear}
            disabled={clearDisabled}
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
        className={inputClass}
      />
    </label>
  );
}

const inputClass =
  "w-full py-2 px-[10px] box-border border border-(--border-01) rounded-(--border-radius-04) text-sm";
const lblClass = "mb-1 text-[13px] font-medium";
