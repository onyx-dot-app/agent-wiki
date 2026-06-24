"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@onyx-ai/opal/components";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { apiFetch } from "@/lib/api";
import { useIsMobile } from "@/lib/viewport";

interface AppSettings {
  warn_update_threshold_default: number;
  auto_update_cap: number;
}

export default function AdminAppSettingsPage() {
  const isMobile = useIsMobile();
  return (
    <RequireAdmin>
      <main
        className="max-w-[720px]"
        style={{ padding: isMobile ? "16px 12px" : "24px 32px" }}
      >
        <BackLink />
        <PageHeader
          title="Auto-update health"
          description="Guardrails for pages that auto-update too often. The warning threshold is the per-page default owners can override; the cap is a hard limit — any page that exceeds it has its auto-update turned off automatically. Set either to 0 to disable it."
        />
        <AppSettingsForm />
      </main>
    </RequireAdmin>
  );
}

function AppSettingsForm() {
  const [warnDefault, setWarnDefault] = useState("");
  const [cap, setCap] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    apiFetch<AppSettings>("/admin/app-settings")
      .then((r) => {
        setWarnDefault(String(r.warn_update_threshold_default));
        setCap(String(r.auto_update_cap));
        setLoaded(true);
      })
      .catch((e) =>
        setError(e instanceof Error ? e.message : "failed to load"),
      );
  }, []);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const r = await apiFetch<AppSettings>("/admin/app-settings", {
        method: "PUT",
        body: JSON.stringify({
          warn_update_threshold_default: Number(warnDefault),
          auto_update_cap: Number(cap),
        }),
      });
      setWarnDefault(String(r.warn_update_threshold_default));
      setCap(String(r.auto_update_cap));
      setSaved("Saved.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    } finally {
      setSaving(false);
    }
  }

  if (!loaded && !error) return <LoadingSpinner />;

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <label>
        <div className={lblClass}>
          Default warning threshold (updates / 24h)
        </div>
        <input
          type="number"
          min={0}
          value={warnDefault}
          onChange={(e) => setWarnDefault(e.target.value)}
          className={inputClass}
        />
      </label>

      <label>
        <div className={lblClass}>Auto-update cap (updates / 24h)</div>
        <input
          type="number"
          min={0}
          value={cap}
          onChange={(e) => setCap(e.target.value)}
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
  "w-full py-2 px-[10px] box-border border border-(--border-01) rounded-(--border-radius-04) text-sm";
const lblClass = "mb-1 text-[13px] font-medium";
