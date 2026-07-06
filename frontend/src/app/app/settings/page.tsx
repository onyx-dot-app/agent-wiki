"use client";

import { Suspense, useEffect, useMemo, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import {
  Button,
  InputTypeIn,
  LineItemButton,
  LinkButton,
  Text,
} from "@onyx-ai/opal/components";
import { SvgSliders } from "@onyx-ai/opal/icons";
import { SettingsLayouts } from "@onyx-ai/opal/layouts";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { apiFetch } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { effectiveTimezone } from "@/lib/cron";
import { useTheme } from "next-themes";
import type { DefaultLanding, ThemeSetting, UserSettings } from "@/types";

const DEFAULT_SETTINGS: UserSettings = {
  theme: "system",
  timezone: null,
  default_landing: "wiki_home",
  chat_provider: null,
  chat_model: null,
};

// A short curated IANA list — covers the common cases without dumping the
// full ~600-zone list into a native select. The text input below is the
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

const TABS = [
  { key: "general", label: "General" },
  { key: "wiki", label: "Wiki Preferences" },
  { key: "account", label: "Account & Access" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export default function SettingsPage() {
  return (
    <Suspense fallback={<LoadingSpinner center />}>
      <SettingsPageInner />
    </Suspense>
  );
}

function SettingsPageInner() {
  const { user, loading, updateSettings, updateProfile } = useRequireAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const rawTab = searchParams.get("tab");
  const tab: TabKey = TABS.some((t) => t.key === rawTab)
    ? (rawTab as TabKey)
    : "general";

  if (loading || !user) {
    return <LoadingSpinner center />;
  }

  return (
    <SettingsLayouts.Root width="lg">
      <SettingsLayouts.Header
        icon={SvgSliders}
        title="Settings"
        description="Preferences scoped to your account. Saved on the server, so they follow you across browsers."
        divider
      />
      <SettingsLayouts.Body>
        <div className="flex w-full items-start gap-6">
          <nav className="flex w-44 shrink-0 flex-col gap-1">
            {TABS.map((t) => (
              <LineItemButton
                key={t.key}
                title={t.label}
                sizePreset="main-ui"
                variant="body"
                state={tab === t.key ? "selected" : "empty"}
                onClick={() =>
                  router.replace(`/app/settings?tab=${t.key}`, {
                    scroll: false,
                  })
                }
              />
            ))}
          </nav>
          <div className="flex min-w-0 flex-1 flex-col gap-6">
            {tab === "general" && (
              <Section title="General">
                <AppearanceForm
                  initial={user.settings}
                  updateSettings={updateSettings}
                />
              </Section>
            )}
            {tab === "wiki" && (
              <Section title="Wiki Preferences">
                <WikiPrefsForm
                  initial={user.settings}
                  updateSettings={updateSettings}
                />
                <ChatModelForm
                  initial={user.settings}
                  updateSettings={updateSettings}
                />
              </Section>
            )}
            {tab === "account" && (
              <Section title="Account & Access">
                <ProfileForm
                  initialName={user.name}
                  email={user.email}
                  updateProfile={updateProfile}
                />
              </Section>
            )}
          </div>
        </div>
      </SettingsLayouts.Body>
    </SettingsLayouts.Root>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-4">
      <Text as="h2" font="main-content-emphasis" color="text-04">
        {title}
      </Text>
      {children}
    </section>
  );
}

function FieldLabel({ children }: { children: string }) {
  return (
    <div className="mb-1">
      <Text font="main-ui-action" color="text-04">
        {children}
      </Text>
    </div>
  );
}

function FieldHint({ children }: { children: React.ReactNode }) {
  return <div className="mt-1 text-xs text-(--text-03)">{children}</div>;
}

function ProfileForm({
  initialName,
  email,
  updateProfile,
}: {
  initialName: string | null;
  email: string;
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
        <FieldLabel>Display name</FieldLabel>
        <InputTypeIn
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setSaved(false);
            setError(null);
          }}
          placeholder="e.g. Ada Lovelace"
          maxLength={200}
        />
        <FieldHint>
          Shown in the app header and on activity attributed to you. Leave blank
          to fall back to your email.
        </FieldHint>
      </label>

      <div>
        <FieldLabel>Login email</FieldLabel>
        <Text font="main-ui-body" color="text-04">
          {email}
        </Text>
        <FieldHint>
          The address you sign in with. Account emails go here.
        </FieldHint>
      </div>

      <FormStatus error={error} saved={saved} />
      <div>
        <Button type="submit" variant="action" disabled={saving || !dirty}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </form>
  );
}

function FormStatus({
  error,
  saved,
}: {
  error: string | null;
  saved: boolean;
}) {
  if (error)
    return <div className="text-(--status-text-error-05)">{error}</div>;
  if (saved)
    return <div className="text-(--status-text-success-05)">Saved.</div>;
  return null;
}

/** Shared save plumbing for the settings forms: diff the draft against a
 * baseline and PUT only the changed keys, so untouched fields never patch. */
function useSettingsDraft(
  baseline: UserSettings,
  updateSettings: (partial: Partial<UserSettings>) => Promise<UserSettings>,
) {
  const [draft, setDraft] = useState<UserSettings>(baseline);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty = useMemo(
    () =>
      (Object.keys(draft) as (keyof UserSettings)[]).some(
        (k) => draft[k] !== baseline[k],
      ),
    [draft, baseline],
  );

  function update<K extends keyof UserSettings>(
    key: K,
    value: UserSettings[K],
  ) {
    setDraft((d) => ({ ...d, [key]: value }));
    setSaved(false);
    setError(null);
  }

  async function save(onError?: () => void) {
    if (!dirty) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const partial: Partial<UserSettings> = {};
      (Object.keys(draft) as (keyof UserSettings)[]).forEach((k) => {
        if (draft[k] !== baseline[k]) {
          (partial as Record<string, unknown>)[k] = draft[k];
        }
      });
      await updateSettings(partial);
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
      onError?.();
    } finally {
      setSaving(false);
    }
  }

  return { draft, setDraft, update, save, dirty, saving, saved, error };
}

function AppearanceForm({
  initial,
  updateSettings,
}: {
  initial: UserSettings;
  updateSettings: (partial: Partial<UserSettings>) => Promise<UserSettings>;
}) {
  // An unset timezone (null) means "use my local zone" — show that concretely
  // in the form so the field never reads UTC just because nothing was chosen.
  const initialTz = effectiveTimezone(initial.timezone);
  const baseline = useMemo<UserSettings>(
    () => ({ ...DEFAULT_SETTINGS, ...initial, timezone: initialTz }),
    [initial, initialTz],
  );
  const form = useSettingsDraft(baseline, updateSettings);
  const { draft, setDraft, update } = form;
  const [tzCustom, setTzCustom] = useState<boolean>(
    !COMMON_TIMEZONES.includes(initialTz),
  );

  // Pull future updates (e.g. another tab) back into the form.
  useEffect(() => {
    setDraft(baseline);
    setTzCustom(!COMMON_TIMEZONES.includes(baseline.timezone ?? ""));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseline]);

  const { setTheme } = useTheme();

  function pickTheme(theme: ThemeSetting) {
    update("theme", theme);
    // Apply immediately so the user sees the change without waiting for
    // the round-trip — Save still needs to hit the server to persist.
    setTheme(theme);
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        // Revert the optimistic theme apply if the server rejected.
        void form.save(() => setTheme(initial.theme));
      }}
      className="flex flex-col gap-4"
    >
      <label>
        <FieldLabel>Theme</FieldLabel>
        {/* raw-ok: no Opal select component */}
        <select
          value={draft.theme}
          onChange={(e) => pickTheme(e.target.value as ThemeSetting)}
          className={selectClass}
        >
          <option value="system">System (match OS)</option>
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </select>
        <FieldHint>Visual chrome of the app on this account.</FieldHint>
      </label>

      <label>
        <FieldLabel>Timezone</FieldLabel>
        {tzCustom ? (
          <InputTypeIn
            value={draft.timezone ?? ""}
            onChange={(e) => update("timezone", e.target.value)}
            placeholder="e.g. America/Los_Angeles"
          />
        ) : (
          <>
            {/* raw-ok: no Opal select component */}
            <select
              value={draft.timezone ?? ""}
              onChange={(e) => {
                if (e.target.value === "__custom__") {
                  setTzCustom(true);
                  return;
                }
                update("timezone", e.target.value);
              }}
              className={selectClass}
            >
              {COMMON_TIMEZONES.map((tz) => (
                <option key={tz} value={tz}>
                  {tz}
                </option>
              ))}
              <option value="__custom__">Other…</option>
            </select>
          </>
        )}
        <FieldHint>
          Used for timestamps and scheduled-trigger displays.
          {tzCustom && (
            <>
              {" "}
              <LinkButton
                onClick={() => {
                  setTzCustom(false);
                  if (!COMMON_TIMEZONES.includes(draft.timezone ?? "")) {
                    update("timezone", "UTC");
                  }
                }}
              >
                Pick from common list
              </LinkButton>
            </>
          )}
        </FieldHint>
      </label>

      <FormStatus error={form.error} saved={form.saved} />
      <div>
        <Button
          type="submit"
          variant="action"
          disabled={form.saving || !form.dirty}
        >
          {form.saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </form>
  );
}

function WikiPrefsForm({
  initial,
  updateSettings,
}: {
  initial: UserSettings;
  updateSettings: (partial: Partial<UserSettings>) => Promise<UserSettings>;
}) {
  const baseline = useMemo<UserSettings>(
    () => ({ ...DEFAULT_SETTINGS, ...initial }),
    [initial],
  );
  const form = useSettingsDraft(baseline, updateSettings);
  const { draft, setDraft, update } = form;

  useEffect(() => {
    setDraft(baseline);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseline]);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void form.save();
      }}
      className="flex flex-col gap-4"
    >
      <label>
        <FieldLabel>Default landing page</FieldLabel>
        {/* raw-ok: no Opal select component */}
        <select
          value={draft.default_landing}
          onChange={(e) =>
            update("default_landing", e.target.value as DefaultLanding)
          }
          className={selectClass}
        >
          <option value="wiki_home">Wiki home</option>
          <option value="recent">Recently edited</option>
          <option value="last_viewed">Last viewed page</option>
        </select>
        <FieldHint>Where the app opens after sign-in.</FieldHint>
      </label>

      <FormStatus error={form.error} saved={form.saved} />
      <div>
        <Button
          type="submit"
          variant="action"
          disabled={form.saving || !form.dirty}
        >
          {form.saving ? "Saving…" : "Save"}
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
        <FieldLabel>Chat model</FieldLabel>
        <InputTypeIn
          value={chatModel}
          onChange={(e) => {
            setChatModel(e.target.value);
            setSaved(false);
            setError(null);
          }}
          placeholder={placeholder}
        />
        <FieldHint>
          Override the model used in your chat sessions. Leave blank to use the
          admin-configured agent default
          {llmStatus?.configured
            ? ` (currently ${llmStatus.provider} / ${llmStatus.model})`
            : ""}
          .
        </FieldHint>
      </label>
      <FormStatus error={error} saved={saved} />
      <div>
        <Button type="submit" variant="action" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </form>
  );
}

const selectClass =
  "w-full py-2 px-[10px] box-border border border-(--border-02) rounded-(--radius-08) bg-(--background-neutral-00) text-sm";
