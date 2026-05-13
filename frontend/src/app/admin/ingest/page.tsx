"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/common/AppShell";
import { Button } from "@/components/common/Button";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { apiFetch } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { color, radius } from "@/lib/theme";
import { useIsMobile } from "@/lib/viewport";

interface IngestSettings {
  max_doc_chars: number;
  api_key: string | null;
}

export default function AdminIngestPage() {
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
      <main style={{ padding: isMobile ? "16px 12px" : "24px 32px", maxWidth: 720 }}>
        <BackLink />
        <PageHeader
          title="Onyx connection"
          description="Connect your Onyx instance to automatically push indexed documents into this wiki. Copy the base URL and API key below into your Onyx environment variables."
        />
        <IngestForm />
      </main>
    </AppShell>
  );
}

function IngestForm() {
  const [settings, setSettings] = useState<IngestSettings | null>(null);
  const [maxDocChars, setMaxDocChars] = useState("");
  const [keyVisible, setKeyVisible] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [baseUrl, setBaseUrl] = useState("");

  useEffect(() => {
    setBaseUrl(window.location.origin);
  }, []);

  async function load() {
    try {
      const r = await apiFetch<IngestSettings>("/admin/ingest");
      setSettings(r);
      setMaxDocChars(String(r.max_doc_chars));
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
      const r = await apiFetch<IngestSettings>("/admin/ingest", {
        method: "PUT",
        body: JSON.stringify({ max_doc_chars: Number(maxDocChars) }),
      });
      setSettings(r);
      setSaved("Saved.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    } finally {
      setSaving(false);
    }
  }

  async function regenerateKey() {
    if (
      settings?.api_key &&
      !confirm("Regenerate the API key? The old key will stop working immediately.")
    )
      return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const r = await apiFetch<{ api_key: string }>("/admin/ingest/regenerate-key", {
        method: "POST",
      });
      setSettings((prev) => (prev ? { ...prev, api_key: r.api_key } : prev));
      setKeyVisible(true);
      setSaved("New API key generated. Copy it now — it will be masked after you leave this page.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to regenerate");
    } finally {
      setSaving(false);
    }
  }

  async function copyToClipboard(text: string) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // fallback: select text
    }
  }

  if (!settings) return <div>Loading…</div>;

  const dirty = maxDocChars !== String(settings.max_doc_chars);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Connection details */}
      <section>
        <h3 style={{ margin: "0 0 12px", fontSize: 14, fontWeight: 600 }}>Connection details</h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {/* Base URL */}
          <div>
            <div style={lblStyle}>Base URL</div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontFamily: "ui-monospace, monospace", fontSize: 13, color: color.text.primary }}>
                {baseUrl}/api/documents/ingest
              </span>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => void copyToClipboard(`${baseUrl}/api/documents/ingest`)}
              >
                Copy
              </Button>
            </div>
          </div>

          {/* API Key */}
          <div>
            <div style={lblStyle}>API key</div>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                readOnly
                type={keyVisible ? "text" : "password"}
                value={settings.api_key ?? ""}
                placeholder={settings.api_key ? undefined : "No key yet — click Regenerate"}
                style={{ ...inputStyle, flex: 1, fontFamily: settings.api_key ? "ui-monospace, monospace" : undefined }}
              />
              {settings.api_key && keyVisible && (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => void copyToClipboard(settings.api_key ?? "")}
                >
                  Copy
                </Button>
              )}
              <Button
                type="button"
                variant={settings.api_key ? "secondary" : "primary"}
                size="sm"
                disabled={saving}
                onClick={() => void regenerateKey()}
              >
                Regenerate
              </Button>
            </div>
          </div>
        </div>
      </section>

      {/* Max doc size */}
      <form onSubmit={onSubmit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>Ingest settings</h3>
        <label>
          <div style={lblStyle}>Max document size (characters)</div>
          <input
            type="number"
            min={1000}
            max={5000000}
            value={maxDocChars}
            onChange={(e) => setMaxDocChars(e.target.value)}
            style={{ ...inputStyle, width: 160 }}
          />
        </label>
        {error && <div style={{ color: color.state.danger.fg }}>{error}</div>}
        {saved && <div style={{ color: color.state.success.fg }}>{saved}</div>}
        <div>
          <Button type="submit" variant="primary" disabled={saving || !dirty}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </form>
    </div>
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
