"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/common/Button";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { apiFetch } from "@/lib/api";
import { color, radius } from "@/lib/theme";
import { useIsMobile } from "@/lib/viewport";

interface WebSettings {
  search_provider: "serper";
  crawl_provider: "firecrawl";
  serper_api_key_set: boolean;
  firecrawl_api_key_set: boolean;
  serper_api_key_hint: string;
  firecrawl_api_key_hint: string;
}

export default function AdminWebPage() {
  const isMobile = useIsMobile();
  return (
    <RequireAdmin>
      <main style={{ padding: isMobile ? "16px 12px" : "24px 32px", maxWidth: 720 }}>
        <BackLink />
        <PageHeader
          title="Web search & crawl"
          description={
            <>
              Search results come from <strong>Serper</strong>; full page contents come from{" "}
              <strong>Firecrawl</strong>. Keys are stored in the database and never echoed back
              to the browser.
            </>
          }
        />
        <WebForm />
      </main>
    </RequireAdmin>
  );
}

function WebForm() {
  const [settings, setSettings] = useState<WebSettings | null>(null);
  const [serperKey, setSerperKey] = useState("");
  const [firecrawlKey, setFirecrawlKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  async function load() {
    try {
      const r = await apiFetch<WebSettings>("/admin/web");
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
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const body: Record<string, unknown> = {};
      if (serperKey) body.serper_api_key = serperKey;
      if (firecrawlKey) body.firecrawl_api_key = firecrawlKey;
      await apiFetch<WebSettings>("/admin/web", {
        method: "PUT",
        body: JSON.stringify(body),
      });
      setSerperKey("");
      setFirecrawlKey("");
      setSaved(true);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    } finally {
      setSaving(false);
    }
  }

  async function clearKey(field: "serper_api_key" | "firecrawl_api_key") {
    if (!confirm("Clear this API key?")) return;
    setSaving(true);
    setError(null);
    try {
      await apiFetch<WebSettings>("/admin/web", {
        method: "PUT",
        body: JSON.stringify({ [field]: null }),
      });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to clear");
    } finally {
      setSaving(false);
    }
  }

  if (!settings) return <div>Loading…</div>;

  return (
    <form onSubmit={onSubmit} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <ProviderRow label="Search" value="Serper" url="https://serper.dev/" />
      <KeyField
        label="Serper API key"
        value={serperKey}
        onChange={setSerperKey}
        isSet={settings.serper_api_key_set}
        hint={settings.serper_api_key_hint}
        placeholder="serper API key"
        onClear={() => void clearKey("serper_api_key")}
        clearDisabled={saving || !settings.serper_api_key_set}
      />

      <div style={{ height: 1, background: color.border.subtle, margin: "8px 0" }} />

      <ProviderRow label="Crawl" value="Firecrawl" url="https://www.firecrawl.dev/" />
      <KeyField
        label="Firecrawl API key"
        value={firecrawlKey}
        onChange={setFirecrawlKey}
        isSet={settings.firecrawl_api_key_set}
        hint={settings.firecrawl_api_key_hint}
        placeholder="fc-…"
        onClear={() => void clearKey("firecrawl_api_key")}
        clearDisabled={saving || !settings.firecrawl_api_key_set}
      />

      {error && <div style={{ color: color.state.danger.fg }}>{error}</div>}
      {saved && <div style={{ color: color.state.success.fg }}>Saved.</div>}
      <div>
        <Button type="submit" variant="primary" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </form>
  );
}

function ProviderRow({ label, value, url }: { label: string; value: string; url: string }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 8, fontSize: 13 }}>
      <span style={{ color: color.text.muted, width: 60 }}>{label}</span>
      <span style={{ fontWeight: 500 }}>{value}</span>
      <a href={url} target="_blank" rel="noreferrer" style={{ color: color.text.primary, fontSize: 12, textDecoration: "underline" }}>
        get key ↗
      </a>
    </div>
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
