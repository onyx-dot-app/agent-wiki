"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@onyx-ai/opal/components";
import { useConfirm } from "@/components/common/ConfirmDialog";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { apiFetch } from "@/lib/api";
import { useIsMobile } from "@/lib/viewport";

interface SlackAppSettings {
  client_id: string;
  client_secret_set: boolean;
  client_secret_hint: string;
}

export default function AdminSlackPage() {
  const isMobile = useIsMobile();
  return (
    <RequireAdmin>
      <main
        className="max-w-[720px]"
        style={{ padding: isMobile ? "16px 12px" : "24px 32px" }}
      >
        <BackLink />
        <PageHeader
          title="Slack app"
          description="OAuth credentials for the Agent Wiki Slack app (Basic Information → App Credentials on api.slack.com). The Connect Slack flow stays hidden until both are saved."
        />
        <SlackAppForm />
      </main>
    </RequireAdmin>
  );
}

function SlackAppForm() {
  const [settings, setSettings] = useState<SlackAppSettings | null>(null);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const confirmDialog = useConfirm();

  async function load() {
    try {
      const r = await apiFetch<SlackAppSettings>("/admin/slack-app");
      setSettings(r);
      setClientId(r.client_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const body: Record<string, unknown> = { client_id: clientId };
      if (clientSecret) body.client_secret = clientSecret;
      const r = await apiFetch<SlackAppSettings>("/admin/slack-app", {
        method: "PUT",
        body: JSON.stringify(body),
      });
      setSettings(r);
      setClientSecret("");
      setSaved("Saved.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    } finally {
      setSaving(false);
    }
  }

  async function clearSecret() {
    if (
      !(await confirmDialog({
        title: "Clear the Slack client secret?",
        body: "The Connect Slack flow will be hidden until a new secret is set.",
        confirmLabel: "Clear secret",
      }))
    )
      return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const r = await apiFetch<SlackAppSettings>("/admin/slack-app", {
        method: "PUT",
        body: JSON.stringify({ client_id: clientId, client_secret: null }),
      });
      setSettings(r);
      setClientSecret("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to clear");
    } finally {
      setSaving(false);
    }
  }

  if (!settings) return <LoadingSpinner />;

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <label>
        <div className="mb-1 text-[13px] font-medium">Client ID</div>
        <input
          value={clientId}
          onChange={(e) => setClientId(e.target.value)}
          placeholder="1234567890.1234567890123"
          required
          className={inputClass}
        />
      </label>

      <label>
        <div className="mb-1 flex items-center gap-[6px] text-[13px] font-medium">
          <span>Client secret</span>
          {settings.client_secret_set && (
            <span className="font-mono text-xs font-normal text-(--text-03)">
              currently {settings.client_secret_hint}
            </span>
          )}
          <span className="flex-1" />
          {settings.client_secret_set && (
            <Button
              type="button"
              size="sm"
              variant="danger"
              onClick={() => void clearSecret()}
              disabled={saving}
            >
              Clear
            </Button>
          )}
        </div>
        <input
          type="password"
          value={clientSecret}
          onChange={(e) => setClientSecret(e.target.value)}
          placeholder={
            settings.client_secret_set
              ? "leave blank to keep"
              : "secret from api.slack.com"
          }
          className={inputClass}
        />
      </label>

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

const inputClass =
  "w-full py-2 px-[10px] box-border border border-(--border-01) rounded-(--radius-04) text-sm";
