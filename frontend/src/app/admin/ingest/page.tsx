"use client";

import { useEffect, useState, type FormEvent } from "react";
import { mutate as globalMutate } from "swr";

import { Button } from "@onyx-ai/opal/components";
import { useConfirm } from "@/components/common/ConfirmDialog";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { apiFetch } from "@/lib/api";
import {
  ALL_PROVIDERS,
  isConfigured,
  providerLabel,
  type LLMSettings,
} from "@/lib/llm";
import { useIsMobile } from "@/lib/viewport";

interface IngestSettings {
  max_doc_chars: number;
  api_key_set: boolean;
  api_key_hint: string;
  onyx_base_url: string | null;
  warn_update_threshold_default: number;
  auto_update_cap: number;
}

export default function AdminIngestPage() {
  const isMobile = useIsMobile();
  return (
    <RequireAdmin>
      <main className={`max-w-[720px] ${isMobile ? "px-3 py-4" : "px-8 py-6"}`}>
        <BackLink />
        <PageHeader
          title="Onyx connection"
          description="Connect your Onyx instance to automatically push indexed documents into this wiki. Copy the base URL and API key below into your Onyx environment variables."
        />
        <IngestForm />
      </main>
    </RequireAdmin>
  );
}

function IngestForm() {
  const [settings, setSettings] = useState<IngestSettings | null>(null);
  const [llmSettings, setLlmSettings] = useState<LLMSettings | null>(null);
  const [maxDocChars, setMaxDocChars] = useState("");
  const [warnDefault, setWarnDefault] = useState("");
  const [autoCap, setAutoCap] = useState("");
  const [keyVisible, setKeyVisible] = useState(false);
  // Raw key exists client-side only in the regenerate response — show-once.
  const [freshKey, setFreshKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [baseUrl, setBaseUrl] = useState("");
  const [onyxBaseUrl, setOnyxBaseUrl] = useState("");
  const confirmDialog = useConfirm();

  useEffect(() => {
    setBaseUrl(window.location.origin);
  }, []);

  // Auto-dismiss the "Saved." confirmation so it doesn't linger on the page.
  useEffect(() => {
    if (!saved) return;
    const t = setTimeout(() => setSaved(null), 2500);
    return () => clearTimeout(t);
  }, [saved]);

  async function load() {
    try {
      const [ingest, llm] = await Promise.all([
        apiFetch<IngestSettings>("/admin/ingest"),
        apiFetch<LLMSettings>("/admin/llm"),
      ]);
      setSettings(ingest);
      setMaxDocChars(String(ingest.max_doc_chars));
      setOnyxBaseUrl(ingest.onyx_base_url ?? "");
      setWarnDefault(String(ingest.warn_update_threshold_default));
      setAutoCap(String(ingest.auto_update_cap));
      setLlmSettings(llm);
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
        body: JSON.stringify({
          max_doc_chars: Number(maxDocChars),
          onyx_base_url: onyxBaseUrl.trim() || null,
          warn_update_threshold_default: Number(warnDefault),
          auto_update_cap: Number(autoCap),
        }),
      });
      setSettings(r);
      setOnyxBaseUrl(r.onyx_base_url ?? "");
      setWarnDefault(String(r.warn_update_threshold_default));
      setAutoCap(String(r.auto_update_cap));
      setSaved("Saved.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    } finally {
      setSaving(false);
    }
  }

  async function regenerateKey() {
    if (
      (settings?.api_key_set || freshKey) &&
      !(await confirmDialog({
        title: "Regenerate the API key?",
        body: "The old key will stop working immediately.",
        confirmLabel: "Regenerate",
      }))
    )
      return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const r = await apiFetch<{ api_key: string }>(
        "/admin/ingest/regenerate-key",
        {
          method: "POST",
        },
      );
      setFreshKey(r.api_key);
      setSettings((prev) => (prev ? { ...prev, api_key_set: true } : prev));
      setKeyVisible(true);
      setSaved(
        "New API key generated. Copy it now — it will be masked after you leave this page.",
      );
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

  if (!settings || !llmSettings) return <LoadingSpinner />;

  const dirty =
    maxDocChars !== String(settings.max_doc_chars) ||
    (onyxBaseUrl.trim() || "") !== (settings.onyx_base_url ?? "") ||
    warnDefault !== String(settings.warn_update_threshold_default) ||
    autoCap !== String(settings.auto_update_cap);

  return (
    <div className="flex flex-col gap-6">
      {/* Connection details */}
      <section>
        <h3 className="m-0 mb-3 text-sm font-semibold">Connection details</h3>
        <div className="flex flex-col gap-3">
          {/* Base URL */}
          <div>
            <div className="mb-1 text-[13px] font-medium">Base URL</div>
            <div className="flex items-center gap-2">
              <span className="font-mono text-[13px] text-(--text-05)">
                {baseUrl}/api/documents/ingest
              </span>
              <Button
                type="button"
                variant="default"
                size="sm"
                onClick={() =>
                  void copyToClipboard(`${baseUrl}/api/documents/ingest`)
                }
              >
                Copy
              </Button>
            </div>
          </div>

          {/* API Key */}
          <div>
            <div className="mb-1 text-[13px] font-medium">API key</div>
            <div className="flex gap-2">
              <input
                readOnly
                type={keyVisible ? "text" : "password"}
                value={freshKey ?? ""}
                placeholder={
                  freshKey
                    ? undefined
                    : settings.api_key_set
                      ? settings.api_key_hint
                      : "No key yet — click Regenerate"
                }
                className={`box-border w-full flex-1 rounded-(--border-radius-04) border border-(--border-01) px-[10px] py-2 text-sm ${freshKey ? "font-mono" : ""}`}
              />
              {freshKey && keyVisible && (
                <Button
                  type="button"
                  variant="default"
                  size="sm"
                  onClick={() => void copyToClipboard(freshKey)}
                >
                  Copy
                </Button>
              )}
              <Button
                type="button"
                variant={
                  settings.api_key_set || freshKey ? "default" : "action"
                }
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

      {/* Outbound: the Onyx instance this wiki calls (Craft launches). */}
      <form onSubmit={onSubmit} className="flex flex-col gap-3">
        <h3 className="m-0 text-sm font-semibold">Onyx instance</h3>
        <label>
          <div className="mb-1 text-[13px] font-medium">
            Onyx instance URL{" "}
            <span className="font-normal text-(--text-03)">
              — enables launching Onyx Craft from wiki pages
            </span>
          </div>
          <input
            type="url"
            inputMode="url"
            value={onyxBaseUrl}
            onChange={(e) => setOnyxBaseUrl(e.target.value)}
            placeholder="https://your-onyx.example.com"
            className="box-border w-full rounded-(--border-radius-04) border border-(--border-01) px-[10px] py-2 font-mono text-sm"
          />
          <div className="mt-1.5 text-xs text-(--text-03)">
            The public origin of your Onyx deployment (no trailing slash). Users
            then connect their own Onyx token under Agents → Onyx Craft.
          </div>
        </label>

        <h3 className="m-0 mt-2 text-sm font-semibold">Ingest settings</h3>
        <label>
          <div className="mb-1 text-[13px] font-medium">
            Max document size (characters)
          </div>
          <input
            type="number"
            min={1000}
            max={5000000}
            value={maxDocChars}
            onChange={(e) => setMaxDocChars(e.target.value)}
            className="box-border w-[160px] rounded-(--border-radius-04) border border-(--border-01) px-[10px] py-2 text-sm"
          />
        </label>

        <h3 className="m-0 mt-2 text-sm font-semibold">Auto-update health</h3>
        <div className="text-[13px] text-(--text-03)">
          Guardrails for pages that auto-update too often. The warning threshold
          is the per-page default owners can override; any page exceeding the
          cap has its auto-update turned off automatically. Set either to 0 to
          disable it.
        </div>
        <label>
          <div className="mb-1 text-[13px] font-medium">
            Default warning threshold (updates / 24h)
          </div>
          <input
            type="number"
            min={0}
            value={warnDefault}
            onChange={(e) => setWarnDefault(e.target.value)}
            className="box-border w-[160px] rounded-(--border-radius-04) border border-(--border-01) px-[10px] py-2 text-sm"
          />
        </label>
        <label>
          <div className="mb-1 text-[13px] font-medium">
            Auto-update cap (updates / 24h)
          </div>
          <input
            type="number"
            min={0}
            value={autoCap}
            onChange={(e) => setAutoCap(e.target.value)}
            className="box-border w-[160px] rounded-(--border-radius-04) border border-(--border-01) px-[10px] py-2 text-sm"
          />
        </label>
        <div className="flex items-center gap-3">
          <Button type="submit" variant="action" disabled={saving || !dirty}>
            {saving ? "Saving…" : "Save"}
          </Button>
          {error ? (
            <span className="text-[13px] text-(--status-text-error-05)">
              {error}
            </span>
          ) : saved ? (
            <span className="text-[13px] text-(--text-03)">{saved}</span>
          ) : null}
        </div>
      </form>

      <div className="border-t border-(--border-01)" />

      <SelectorModelSection
        settings={llmSettings}
        onSaved={() => void load()}
      />
    </div>
  );
}

function SelectorModelSection({
  settings,
  onSaved,
}: {
  settings: LLMSettings;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [selModel, setSelModel] = useState(
    settings.ingest_selector_model || "",
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const availableProviders = ALL_PROVIDERS.filter((p) =>
    isConfigured(p, settings),
  );
  const options = availableProviders.flatMap((p) =>
    (settings.provider_models[p] ?? []).map((m) => ({ provider: p, model: m })),
  );
  const hasNoModels = availableProviders.length > 0 && options.length === 0;

  useEffect(() => {
    setSelModel(settings.ingest_selector_model || "");
  }, [settings]);

  async function onSave() {
    setSaving(true);
    setError(null);
    try {
      await apiFetch("/admin/llm", {
        method: "PUT",
        body: JSON.stringify({ ingest_selector_model: selModel }),
      });
      setEditing(false);
      onSaved();
      void globalMutate("/llm/status");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    } finally {
      setSaving(false);
    }
  }

  const activeLabel = selModel
    ? selModel === settings.model
      ? `${selModel} (same as main model — pre-filter disabled)`
      : selModel
    : "None — all documents go to the main model";

  return (
    <section>
      <h3 className="m-0 mb-1 text-sm font-semibold">Selector model</h3>
      <div className="mb-3 text-[13px] text-(--text-03)">
        A faster, cheaper model that screens incoming documents before the main
        model decides what to update. Helps reduce cost when many documents are
        being pushed. Leave unset to send all documents straight to the main
        model.
      </div>
      {!editing ? (
        <div className="flex items-center justify-between rounded-(--border-radius-08) border border-(--border-01) bg-(--background-tint-01) px-4 py-3">
          <span
            className={`text-sm ${selModel && selModel !== settings.model ? "text-(--text-05)" : "text-(--text-03)"}`}
          >
            {activeLabel}
          </span>
          <Button
            size="sm"
            variant="default"
            onClick={() => setEditing(true)}
            disabled={availableProviders.length === 0}
          >
            Edit
          </Button>
        </div>
      ) : (
        <div className="flex flex-col rounded-(--border-radius-08) border border-(--border-01) bg-(--background-tint-01)">
          <div className="flex flex-col gap-1 p-3">
            {hasNoModels && (
              <div className="py-1 pb-2 text-[13px] text-(--text-03)">
                No models configured. Add models on the{" "}
                <a
                  href="/admin/language-models"
                  className="text-(--text-inverted-05)"
                >
                  Language models
                </a>{" "}
                page first.
              </div>
            )}
            <button
              type="button"
              onClick={() => setSelModel("")}
              className={`flex cursor-pointer items-center gap-3 rounded-(--border-radius-04) border px-3 py-[10px] text-left ${selModel === "" ? "border-(--border-01) bg-(--background-tint-03)" : "border-(--border-01) bg-(--background-tint-00)"}`}
            >
              <div
                className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full ${selModel === "" ? "bg-(--background-tint-inverted-00)" : "border-[1.5px] border-(--border-02) bg-transparent"}`}
              >
                {selModel === "" && (
                  <div className="h-2 w-2 rounded-full bg-(--text-inverted-05)" />
                )}
              </div>
              <span
                className={`text-[13px] italic ${selModel === "" ? "text-(--text-05)" : "text-(--text-03)"}`}
              >
                None — all documents go to the main model
              </span>
            </button>
            {options.map(({ provider: p, model: m }) => {
              const isSelected = selModel === m;
              return (
                <button
                  key={`${p}:${m}`}
                  type="button"
                  onClick={() => setSelModel(m)}
                  className={`flex cursor-pointer items-center gap-3 rounded-(--border-radius-04) border px-3 py-[10px] text-left ${isSelected ? "border-(--border-01) bg-(--background-tint-03)" : "border-(--border-01) bg-(--background-tint-00)"}`}
                >
                  <div
                    className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full ${isSelected ? "bg-(--background-tint-inverted-00)" : "border-[1.5px] border-(--border-02) bg-transparent"}`}
                  >
                    {isSelected && (
                      <div className="h-2 w-2 rounded-full bg-(--text-inverted-05)" />
                    )}
                  </div>
                  <span
                    className={`shrink-0 text-[13px] font-medium ${isSelected ? "text-(--text-05)" : "text-(--text-04)"}`}
                  >
                    {providerLabel(p, settings)}
                  </span>
                  <span
                    className={`font-mono text-[13px] ${isSelected ? "text-(--text-05)" : "text-(--text-03)"}`}
                  >
                    {m}
                  </span>
                  {m === settings.model && (
                    <span className="ml-auto text-[11px] text-(--text-03)">
                      same as main — pre-filter disabled
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          {error && (
            <div className="px-3 pb-2 text-[13px] text-(--status-text-error-05)">
              {error}
            </div>
          )}
          <div className="flex gap-2 px-3 pt-1 pb-3">
            <Button
              type="button"
              variant="action"
              size="sm"
              disabled={saving}
              onClick={() => void onSave()}
            >
              {saving ? "Saving…" : "Save"}
            </Button>
            <Button
              type="button"
              variant="default"
              size="sm"
              onClick={() => {
                setEditing(false);
                setError(null);
                setSelModel(settings.ingest_selector_model || "");
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
