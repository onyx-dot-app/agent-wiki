"use client";

import {
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import { Button } from "@onyx-ai/opal/components";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { apiFetch } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { setLocalThemePreview } from "@/lib/theme-provider";
import type { DefaultLanding, ThemeSetting, UserSettings } from "@/types";
import styles from "./page.module.css";

const DEFAULT_SETTINGS: UserSettings = {
  theme: "system",
  timezone: "UTC",
  default_landing: "wiki_home",
  chat_provider: null,
  chat_model: null,
};

// A short curated IANA list — covers the common cases without dumping
// the full ~600-zone list into a <select>. The text input below is the
// escape hatch for anything else.
const COMMON_TIMEZONES = [
  "UTC",
  "America/Los_Angeles",
  "America/Denver",
  "America/Chicago",
  "America/New_York",
  "America/Sao_Paulo",
  "Europe/London",
  "Europe/Berlin",
  "Europe/Athens",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
];

export default function SettingsPage() {
  const { user, loading, updateSettings, updateProfile } = useRequireAuth();

  if (loading || !user) {
    return (
      <main className={styles.loading}>
        <LoadingSpinner center />
      </main>
    );
  }

  return (
    <main className={styles.main}>
      <BackLink href="/" label="← Home" />
      <PageHeader
        title="Personal settings"
        description="Profile fields and preferences scoped to your account. Saved on the server, so they follow you across browsers."
      />
      <Section title="Profile">
        <ProfileForm initialName={user.name} updateProfile={updateProfile} />
      </Section>
      <Section title="Preferences">
        <SettingsForm initial={user.settings} updateSettings={updateSettings} />
      </Section>
      <Section title="Chat model">
        <ChatModelForm
          initial={user.settings}
          updateSettings={updateSettings}
        />
      </Section>
    </main>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-6">
      <h2 className="m-0 mb-3 text-sm font-semibold tracking-[0.4px] text-(--text-03) uppercase">
        {title}
      </h2>
      {children}
    </section>
  );
}

function ProfileForm({
  initialName,
  updateProfile,
}: {
  initialName: string | null;
  updateProfile: (partial: { name: string }) => Promise<unknown>;
}) {
  const [name, setName] = useState<string>(initialName ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setName(initialName ?? "");
  }, [initialName]);

  const dirty = name !== (initialName ?? "");

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!dirty) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await updateProfile({ name });
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <label>
        <div className={lblClass}>Display name</div>
        <input
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setSaved(false);
            setError(null);
          }}
          placeholder="e.g. Ada Lovelace"
          maxLength={200}
          className={inputClass}
        />
        <div className={hintClass}>
          Shown in the app header and on activity attributed to you. Leave blank
          to fall back to your email.
        </div>
      </label>

      {error && <div className="text-(--status-text-error-05)">{error}</div>}
      {saved && <div className="text-(--status-text-success-05)">Saved.</div>}
      <div>
        <Button type="submit" variant="action" disabled={saving || !dirty}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </form>
  );
}

function SettingsForm({
  initial,
  updateSettings,
}: {
  initial: UserSettings;
  updateSettings: (partial: Partial<UserSettings>) => Promise<UserSettings>;
}) {
  const [draft, setDraft] = useState<UserSettings>({
    ...DEFAULT_SETTINGS,
    ...initial,
  });
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tzCustom, setTzCustom] = useState<boolean>(
    !COMMON_TIMEZONES.includes(initial.timezone),
  );

  // Pull future updates (e.g. another tab) back into the form.
  useEffect(() => {
    setDraft({ ...DEFAULT_SETTINGS, ...initial });
    setTzCustom(!COMMON_TIMEZONES.includes(initial.timezone));
  }, [initial]);

  const dirty = useMemo(() => {
    return (Object.keys(draft) as (keyof UserSettings)[]).some(
      (k) => draft[k] !== initial[k],
    );
  }, [draft, initial]);

  function update<K extends keyof UserSettings>(
    key: K,
    value: UserSettings[K],
  ) {
    setDraft((d) => ({ ...d, [key]: value }));
    setSaved(false);
    setError(null);
  }

  function pickTheme(theme: ThemeSetting) {
    update("theme", theme);
    // Apply immediately so the user sees the change without waiting for
    // the round-trip — Save still needs to hit the server to persist.
    setLocalThemePreview(theme);
  }

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!dirty) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const partial: Partial<UserSettings> = {};
      (Object.keys(draft) as (keyof UserSettings)[]).forEach((k) => {
        if (draft[k] !== initial[k]) {
          (partial as Record<string, unknown>)[k] = draft[k];
        }
      });
      await updateSettings(partial);
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
      // Revert the optimistic theme apply if the server rejected.
      setLocalThemePreview(initial.theme);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <label>
        <div className={lblClass}>Theme</div>
        <select
          value={draft.theme}
          onChange={(e) => pickTheme(e.target.value as ThemeSetting)}
          className={inputClass}
        >
          <option value="system">System (match OS)</option>
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </select>
        <div className={hintClass}>
          Visual chrome of the app on this account.
        </div>
      </label>

      <label>
        <div className={lblClass}>Timezone</div>
        {tzCustom ? (
          <input
            value={draft.timezone}
            onChange={(e) => update("timezone", e.target.value)}
            placeholder="e.g. America/Los_Angeles"
            className={inputClass}
          />
        ) : (
          <select
            value={draft.timezone}
            onChange={(e) => {
              if (e.target.value === "__custom__") {
                setTzCustom(true);
                return;
              }
              update("timezone", e.target.value);
            }}
            className={inputClass}
          >
            {COMMON_TIMEZONES.map((tz) => (
              <option key={tz} value={tz}>
                {tz}
              </option>
            ))}
            <option value="__custom__">Other…</option>
          </select>
        )}
        <div className={hintClass}>
          Used for timestamps and scheduled-trigger displays.
          {tzCustom && (
            <>
              {" "}
              <button
                type="button"
                onClick={() => {
                  setTzCustom(false);
                  if (!COMMON_TIMEZONES.includes(draft.timezone)) {
                    update("timezone", "UTC");
                  }
                }}
                className="cursor-pointer border-none bg-transparent p-0 text-xs text-(--text-03) underline"
              >
                Pick from common list
              </button>
            </>
          )}
        </div>
      </label>

      <label>
        <div className={lblClass}>Default landing page</div>
        <select
          value={draft.default_landing}
          onChange={(e) =>
            update("default_landing", e.target.value as DefaultLanding)
          }
          className={inputClass}
        >
          <option value="wiki_home">Wiki home</option>
          <option value="recent">Recently edited</option>
          <option value="last_viewed">Last viewed page</option>
        </select>
        <div className={hintClass}>Where the app opens after sign-in.</div>
      </label>

      {error && <div className="text-(--status-text-error-05)">{error}</div>}
      {saved && <div className="text-(--status-text-success-05)">Saved.</div>}
      <div>
        <Button type="submit" variant="action" disabled={saving || !dirty}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </form>
  );
}

interface LLMStatus {
  configured: boolean;
  provider: string;
  model: string;
}

function ChatModelForm({
  initial,
  updateSettings,
}: {
  initial: UserSettings;
  updateSettings: (partial: Partial<UserSettings>) => Promise<UserSettings>;
}) {
  const [chatModel, setChatModel] = useState<string>(initial.chat_model ?? "");
  const [llmStatus, setLlmStatus] = useState<LLMStatus | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setChatModel(initial.chat_model ?? "");
  }, [initial.chat_model]);

  useEffect(() => {
    apiFetch<LLMStatus>("/llm/status")
      .then(setLlmStatus)
      .catch(() => null);
  }, []);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await updateSettings({ chat_model: chatModel.trim() || null });
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    } finally {
      setSaving(false);
    }
  }

  const placeholder = llmStatus?.model
    ? `${llmStatus.model} (agent default)`
    : "leave blank to use agent default";

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <label>
        <div className={lblClass}>Chat model</div>
        <input
          value={chatModel}
          onChange={(e) => {
            setChatModel(e.target.value);
            setSaved(false);
            setError(null);
          }}
          placeholder={placeholder}
          className={inputClass}
        />
        <div className={hintClass}>
          Override the model used in your chat sessions. Leave blank to use the
          admin-configured agent default
          {llmStatus?.configured
            ? ` (currently ${llmStatus.provider} / ${llmStatus.model})`
            : ""}
          .
        </div>
      </label>
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

const inputClass =
  "w-full py-2 px-[10px] box-border border border-(--border-01) rounded-(--border-radius-04) text-sm";
const lblClass = "mb-1 text-[13px] font-medium";
const hintClass = "mt-1 text-xs text-(--text-03) leading-[1.5]";
