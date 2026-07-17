"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@onyx-ai/opal/components";
import { useConfirm } from "@/components/common/ConfirmDialog";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { apiFetch } from "@/lib/api";
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
      <main
        className="max-w-[720px]"
        style={{ padding: isMobile ? "16px 12px" : "24px 32px" }}
      >
        <BackLink />
        <PageHeader
          title="Web search & crawl"
          description={
            <>
              Search results come from <strong>Serper</strong>; full page
              contents come from <strong>Firecrawl</strong>. Keys are stored in
              the database and never echoed back to the browser.
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
  const confirmDialog = useConfirm();

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
    if (
      !(await confirmDialog({
        title: "Clear this API key?",
        confirmLabel: "Clear key",
      }))
    )
      return;
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

  if (!settings) return <LoadingSpinner />;

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
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

      <div className="my-2 h-px bg-(--border-01)" />

      <ProviderRow
        label="Crawl"
        value="Firecrawl"
        url="https://www.firecrawl.dev/"
      />
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

      {error && <div className="text-(--status-text-error-05)">{error}</div>}
      {saved && <div className="text-(--status-text-success-05)">Saved.</div>}
      <div>
        <Button type="submit" variant="action" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </form>
  );
}

function ProviderRow({
  label,
  value,
  url,
}: {
  label: string;
  value: string;
  url: string;
}) {
  return (
    <div className="flex items-baseline gap-2 text-[13px]">
      <span className="w-[60px] text-(--text-03)">{label}</span>
      <span className="font-medium">{value}</span>
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        className="text-xs text-(--text-05) underline"
      >
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
  "w-full py-2 px-[10px] box-border border border-(--border-01) rounded-(--radius-04) text-sm";
