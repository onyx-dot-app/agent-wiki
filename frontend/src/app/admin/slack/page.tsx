"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/common/Button";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { apiFetch } from "@/lib/api";
import { color, radius } from "@/lib/theme";
import { useIsMobile } from "@/lib/viewport";

interface SlackSettings {
  webhook_url_set: boolean;
  webhook_url_hint: string;
  enabled: boolean;
}

export default function AdminSlackPage() {
  const isMobile = useIsMobile();
  return (
    <RequireAdmin>
      <main style={{ padding: isMobile ? "16px 12px" : "24px 32px", maxWidth: 720 }}>
        <BackLink />
        <PageHeader
          title="Slack delivery"
          description="Post trigger fires to a Slack channel via an incoming webhook. A trigger delivers to Slack only when its destination is set to Slack AND the toggle below is on. Create an incoming webhook in Slack (Apps → Incoming Webhooks) and paste its URL here."
        />
        <SlackForm />
      </main>
    </RequireAdmin>
  );
}

function SlackForm() {
  const [settings, setSettings] = useState<SlackSettings | null>(null);
  const [webhookUrl, setWebhookUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function load() {
    try {
      const r = await apiFetch<SlackSettings>("/admin/slack");
      setSettings(r);
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
      const body: Record<string, unknown> = { enabled: settings.enabled };
      if (webhookUrl) body.webhook_url = webhookUrl;
      const r = await apiFetch<SlackSettings>("/admin/slack", {
        method: "PUT",
        body: JSON.stringify(body),
      });
      setSettings(r);
      setWebhookUrl("");
      setSaved("Saved.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    } finally {
      setSaving(false);
    }
  }

  async function clearWebhook() {
    if (!confirm("Clear the Slack webhook URL? Delivery will be disabled until a new URL is set.")) return;
    if (!settings) return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const r = await apiFetch<SlackSettings>("/admin/slack", {
        method: "PUT",
        body: JSON.stringify({ webhook_url: null, enabled: false }),
      });
      setSettings(r);
      setWebhookUrl("");
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
      const r = await apiFetch<SlackSettings>("/admin/slack", {
        method: "PUT",
        body: JSON.stringify({ enabled: next }),
      });
      setSettings(r);
      setSaved(next ? "Slack delivery enabled." : "Slack delivery disabled.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to update");
    } finally {
      setSaving(false);
    }
  }

  if (!settings) return <LoadingSpinner />;

  // Server forces enabled=false unless a webhook URL is set. Mirror that
  // gating so the toggle is greyed out until a URL has been saved.
  const canEnable = settings.webhook_url_set;
  const fieldsDirty = webhookUrl.length > 0;

  return (
    <form onSubmit={onSubmit} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <label>
        <div style={{ ...lblStyle, display: "flex", alignItems: "center", gap: 6 }}>
          <span>Slack incoming webhook URL</span>
          {settings.webhook_url_set && (
            <span style={hintStyle}>currently {settings.webhook_url_hint}</span>
          )}
          <span style={{ flex: 1 }} />
          {settings.webhook_url_set && (
            <Button
              type="button"
              size="sm"
              variant="danger"
              onClick={() => void clearWebhook()}
              disabled={saving}
              style={{ padding: "2px 8px", fontSize: 12 }}
            >
              Clear
            </Button>
          )}
        </div>
        <input
          type="password"
          value={webhookUrl}
          onChange={(e) => setWebhookUrl(e.target.value)}
          placeholder={
            settings.webhook_url_set
              ? "leave blank to keep"
              : "https://hooks.slack.com/services/…"
          }
          style={inputStyle}
        />
      </label>

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
            Slack delivery is currently <strong>{settings.enabled ? "ON" : "OFF"}</strong>
          </div>
          <div style={{ fontSize: 12, color: color.text.muted, marginTop: 2 }}>
            {canEnable
              ? "When on, triggers whose destination is Slack post their message to the webhook."
              : "Save a webhook URL first to enable Slack delivery."}
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
