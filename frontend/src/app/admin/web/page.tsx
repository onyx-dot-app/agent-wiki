"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/common/AppShell";
import { apiFetch } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";

interface WebSettings {
  search_provider: "serper";
  crawl_provider: "firecrawl";
  serper_api_key_set: boolean;
  firecrawl_api_key_set: boolean;
  serper_api_key_hint: string;
  firecrawl_api_key_hint: string;
}

export default function AdminWebPage() {
  const { user, loading } = useRequireAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && user && !user.is_admin) router.replace("/");
  }, [loading, user, router]);

  if (loading || !user) return <main style={{ padding: 32 }}>Loading…</main>;
  if (!user.is_admin) return null;

  return (
    <AppShell>
      <main style={{ padding: 32, maxWidth: 720 }}>
        <BackLink />
        <h1 style={{ marginTop: 8 }}>Web search & crawl</h1>
        <p style={{ color: "#666", marginTop: 0 }}>
          Search results come from <strong>Serper</strong>; full page contents come from{" "}
          <strong>Firecrawl</strong>. Keys are stored in the database and never echoed back
          to the browser.
        </p>
        <WebForm />
      </main>
    </AppShell>
  );
}

function BackLink() {
  return (
    <Link href="/admin" style={{ fontSize: 13, color: "#4f46e5", textDecoration: "none" }}>
      ← Admin
    </Link>
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

      <div style={{ height: 1, background: "#eee", margin: "8px 0" }} />

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

      {error && <div style={{ color: "crimson" }}>{error}</div>}
      {saved && <div style={{ color: "#15803d" }}>Saved.</div>}
      <div>
        <button type="submit" disabled={saving} style={{ ...btnStyle, padding: "10px 20px" }}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}

function ProviderRow({ label, value, url }: { label: string; value: string; url: string }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 8, fontSize: 13 }}>
      <span style={{ color: "#666", width: 60 }}>{label}</span>
      <span style={{ fontWeight: 500 }}>{value}</span>
      <a href={url} target="_blank" rel="noreferrer" style={{ color: "#4f46e5", fontSize: 12 }}>
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
          <button
            type="button"
            onClick={onClear}
            disabled={clearDisabled}
            style={{ ...btnStyle, color: "#b91c1c", padding: "2px 8px", fontSize: 12 }}
          >
            Clear
          </button>
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

const btnStyle: React.CSSProperties = {
  padding: "6px 12px",
  border: "1px solid #d4d4d8",
  background: "white",
  borderRadius: 4,
  cursor: "pointer",
  fontSize: 13,
};
const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: 8,
  boxSizing: "border-box",
  border: "1px solid #d4d4d8",
  borderRadius: 4,
  fontSize: 14,
};
const lblStyle: React.CSSProperties = { marginBottom: 4, fontSize: 13, fontWeight: 500 };
const hintStyle: React.CSSProperties = {
  fontWeight: 400,
  color: "#888",
  fontFamily: "ui-monospace, monospace",
  fontSize: 12,
};
