"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import {
  Button,
  InputTypeIn,
  LineItemButton,
  Popover,
  PopoverMenu,
  SelectButton,
  Switch,
  Text,
} from "@onyx-ai/opal/components";
import {
  SvgBook,
  SvgChevronDown,
  SvgClock,
  SvgEdit,
  SvgHistory,
  SvgLock,
  SvgMoon,
  SvgSliders,
  SvgSun,
} from "@onyx-ai/opal/icons";
import {
  InputErrorText,
  InputHorizontal,
  Section as LayoutSection,
  SettingsLayouts,
} from "@onyx-ai/opal/layouts";
import { ConnectorsTab } from "@/components/settings/ConnectorsTab";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { apiFetch } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { effectiveTimezone } from "@/lib/cron";
import { useTheme } from "next-themes";
import type { DefaultLanding, ThemeSetting, UserSettings } from "@/types";

// A short curated IANA list — covers the common cases without dumping the
// full ~600-zone list into one menu. Other… is the escape hatch.
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
  { key: "notifications", label: "Notifications" },
  { key: "account", label: "Account & Access" },
  { key: "connectors", label: "Connectors" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

type UpdateSettings = (partial: Partial<UserSettings>) => Promise<UserSettings>;

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
    <SettingsLayouts.Root width="md">
      <SettingsLayouts.Header icon={SvgSliders} title="Settings" divider />
      <SettingsLayouts.Body>
        <div className="flex w-full items-start gap-6 max-md:flex-col">
          <nav className="flex w-44 shrink-0 flex-col gap-1 max-md:w-full max-md:flex-row max-md:flex-wrap">
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
              <>
                <SettingsSection title="Profile">
                  <SettingsCard>
                    <TextFieldRow
                      title="Full Name"
                      description="We'll display this name in the app."
                      placeholder="Your name"
                      value={user.name ?? ""}
                      onSave={(v) => updateProfile({ name: v })}
                    />
                    <TextFieldRow
                      title="Work Role"
                      description="Share your role to better tailor responses."
                      placeholder="Your role"
                      value={user.settings.work_role ?? ""}
                      onSave={(v) =>
                        updateSettings({ work_role: v.trim() || null })
                      }
                    />
                  </SettingsCard>
                </SettingsSection>
                <SettingsSection title="Appearance">
                  <SettingsCard>
                    <ColorModeRow
                      settings={user.settings}
                      updateSettings={updateSettings}
                    />
                  </SettingsCard>
                </SettingsSection>
              </>
            )}
            {tab === "wiki" && (
              <SettingsSection title="Wiki">
                <SettingsCard>
                  <DefaultModelRow
                    settings={user.settings}
                    updateSettings={updateSettings}
                  />
                </SettingsCard>
                <SettingsCard>
                  <TimezoneRow
                    settings={user.settings}
                    updateSettings={updateSettings}
                  />
                  <LandingPageRow
                    settings={user.settings}
                    updateSettings={updateSettings}
                  />
                </SettingsCard>
              </SettingsSection>
            )}
            {tab === "notifications" && (
              <SettingsSection title="Notifications">
                <SettingsCard>
                  <SwitchRow
                    title="Comment Emails"
                    description="Email your login address when someone comments on a page you own or mentions you."
                    settingsKey="notify_comment_email"
                    settings={user.settings}
                    updateSettings={updateSettings}
                  />
                  <SwitchRow
                    title="Auto-Update Warning Emails"
                    description="Email your login address when a page you own passes the update warning threshold or hits the cap."
                    settingsKey="notify_update_warning_email"
                    settings={user.settings}
                    updateSettings={updateSettings}
                  />
                </SettingsCard>
              </SettingsSection>
            )}
            {tab === "connectors" && (
              <SettingsSection title="Connectors">
                <ConnectorsTab />
              </SettingsSection>
            )}
            {tab === "account" && (
              <SettingsSection title="Account">
                <SettingsCard>
                  <InputHorizontal
                    center
                    title="Email"
                    description="This is your Onyx user name."
                  >
                    <Text font="main-ui-body" color="text-04" nowrap>
                      {user.email}
                    </Text>
                  </InputHorizontal>
                  <ChangePasswordRow />
                </SettingsCard>
              </SettingsSection>
            )}
          </div>
        </div>
      </SettingsLayouts.Body>
    </SettingsLayouts.Root>
  );
}

function SettingsSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <LayoutSection flexDirection="column" alignItems="start" gap={0.75}>
      <span className="px-[2px]">
        <Text font="main-content-emphasis" color="text-04">
          {title}
        </Text>
      </span>
      <div className="flex w-full flex-col gap-4">{children}</div>
    </LayoutSection>
  );
}

/** The mock's settings card: rows stacked inside one white bordered shell. */
function SettingsCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="box-border flex w-full flex-col gap-4 rounded-(--radius-16) border border-(--border-01) bg-(--background-tint-00) p-4">
      {children}
    </div>
  );
}

/** Right-hand control slot of a row, capped at the mock's 240px. */
function ControlSlot({ children }: { children: React.ReactNode }) {
  return <div className="w-full max-w-[240px]">{children}</div>;
}

/** Text field row that saves on blur (or Enter) when the value changed. */
function TextFieldRow({
  title,
  description,
  placeholder,
  value,
  onSave,
}: {
  title: string;
  description: string;
  placeholder: string;
  value: string;
  onSave: (next: string) => Promise<unknown>;
}) {
  const [draft, setDraft] = useState(value);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  async function commit() {
    if (draft === value) return;
    setError(null);
    try {
      await onSave(draft);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    }
  }

  return (
    <div className="flex w-full flex-col gap-1">
      <InputHorizontal center title={title} description={description}>
        <ControlSlot>
          <InputTypeIn
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setError(null);
            }}
            onBlur={() => void commit()}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
            }}
            placeholder={placeholder}
          />
        </ControlSlot>
      </InputHorizontal>
      {error && <InputErrorText type="error">{error}</InputErrorText>}
    </div>
  );
}

/** Select-style row: SelectButton trigger + popover menu, saving on pick. */
function SelectRow({
  title,
  description,
  label,
  icon,
  error,
  children,
  open,
  onOpenChange,
}: {
  title: string;
  description: string;
  label: string;
  icon?: React.ComponentProps<typeof SelectButton>["icon"];
  error?: string | null;
  children: React.ReactNode[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <div className="flex w-full flex-col gap-1">
      <InputHorizontal center title={title} description={description}>
        <div className="w-[240px] shrink-0 rounded-(--radius-08) border border-(--border-02) bg-(--background-neutral-00) [&_.opal-select-button]:w-full [&_.opal-select-button>*:nth-last-child(1)]:ml-auto">
          <Popover open={open} onOpenChange={onOpenChange}>
            <Popover.Trigger asChild>
              <SelectButton
                icon={icon}
                rightIcon={SvgChevronDown}
                size="sm"
                state="empty"
                width="full"
              >
                {label}
              </SelectButton>
            </Popover.Trigger>
            <Popover.Content width="trigger" align="end" sideOffset={4}>
              <PopoverMenu>{children}</PopoverMenu>
            </Popover.Content>
          </Popover>
        </div>
      </InputHorizontal>
      {error && <InputErrorText type="error">{error}</InputErrorText>}
    </div>
  );
}

function ColorModeRow({
  settings,
  updateSettings,
}: {
  settings: UserSettings;
  updateSettings: UpdateSettings;
}) {
  const { setTheme, systemTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const systemLabel = systemTheme === "dark" ? "Dark" : "Light";
  const label =
    settings.theme === "system"
      ? `Auto (${systemLabel})`
      : settings.theme === "dark"
        ? "Dark"
        : "Light";

  async function pick(theme: ThemeSetting) {
    setOpen(false);
    setError(null);
    setTheme(theme);
    try {
      await updateSettings({ theme });
    } catch (e) {
      // Revert the optimistic apply if the server rejected.
      setTheme(settings.theme);
      setError(e instanceof Error ? e.message : "failed to save");
    }
  }

  return (
    <SelectRow
      title="Color Mode"
      description="Select your preferred color mode for the UI."
      label={label}
      icon={settings.theme === "dark" ? SvgMoon : SvgSun}
      error={error}
      open={open}
      onOpenChange={setOpen}
    >
      {(
        [
          ["system", `Auto (${systemLabel})`],
          ["light", "Light"],
          ["dark", "Dark"],
        ] as const
      ).map(([value, title]) => (
        <LineItemButton
          key={value}
          title={title}
          sizePreset="main-ui"
          variant="body"
          state={settings.theme === value ? "selected" : "empty"}
          onClick={() => void pick(value)}
        />
      ))}
    </SelectRow>
  );
}

interface LLMStatus {
  configured: boolean;
  provider: string;
  model: string;
}

function DefaultModelRow({
  settings,
  updateSettings,
}: {
  settings: UserSettings;
  updateSettings: UpdateSettings;
}) {
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState(false);
  const [draft, setDraft] = useState(settings.chat_model ?? "");
  const [llmStatus, setLlmStatus] = useState<LLMStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<LLMStatus>("/llm/status")
      .then(setLlmStatus)
      .catch(() => null);
  }, []);

  useEffect(() => {
    setDraft(settings.chat_model ?? "");
  }, [settings.chat_model]);

  const shortModel = llmStatus?.model?.split(/[/.]/).pop();
  const systemLabel = `System Default${shortModel ? ` (${shortModel})` : ""}`;
  const label = settings.chat_model ?? systemLabel;

  async function save(model: string | null) {
    setError(null);
    try {
      await updateSettings({ chat_model: model });
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    }
  }

  if (custom) {
    return (
      <div className="flex w-full flex-col gap-1">
        <InputHorizontal
          center
          title="Default Model"
          description="This model will be used by Onyx by default in your chats."
        >
          <ControlSlot>
            <InputTypeIn
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => {
                setCustom(false);
                void save(draft.trim() || null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") e.currentTarget.blur();
              }}
              placeholder="provider/model id"
              autoFocus
            />
          </ControlSlot>
        </InputHorizontal>
        {error && <InputErrorText type="error">{error}</InputErrorText>}
      </div>
    );
  }

  return (
    <SelectRow
      title="Default Model"
      description="This model will be used by Onyx by default in your chats."
      label={label}
      error={error}
      open={open}
      onOpenChange={setOpen}
    >
      <LineItemButton
        title={systemLabel}
        sizePreset="main-ui"
        variant="body"
        state={settings.chat_model ? "empty" : "selected"}
        onClick={() => {
          setOpen(false);
          void save(null);
        }}
      />
      <LineItemButton
        icon={SvgEdit}
        title="Custom model…"
        sizePreset="main-ui"
        variant="body"
        state={settings.chat_model ? "selected" : "empty"}
        onClick={() => {
          setOpen(false);
          setCustom(true);
        }}
      />
    </SelectRow>
  );
}

function TimezoneRow({
  settings,
  updateSettings,
}: {
  settings: UserSettings;
  updateSettings: UpdateSettings;
}) {
  const current = effectiveTimezone(settings.timezone);
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState(false);
  const [draft, setDraft] = useState(current);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDraft(effectiveTimezone(settings.timezone));
  }, [settings.timezone]);

  async function save(tz: string) {
    setError(null);
    try {
      await updateSettings({ timezone: tz });
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    }
  }

  async function saveCleared() {
    setError(null);
    try {
      await updateSettings({ timezone: null });
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    }
  }

  if (custom) {
    return (
      <div className="flex w-full flex-col gap-1">
        <InputHorizontal
          center
          title="Timezone"
          description="Default for time-based scheduled triggers."
        >
          <ControlSlot>
            <InputTypeIn
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => {
                setCustom(false);
                const next = draft.trim();
                // Clearing the field drops the override back to the local zone.
                if (!next) void saveCleared();
                else if (next !== current) void save(next);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") e.currentTarget.blur();
              }}
              placeholder="e.g. America/Los_Angeles"
              autoFocus
            />
          </ControlSlot>
        </InputHorizontal>
        {error && <InputErrorText type="error">{error}</InputErrorText>}
      </div>
    );
  }

  return (
    <SelectRow
      title="Timezone"
      description="Default for time-based scheduled triggers."
      label={current.replace(/_/g, " ")}
      icon={SvgClock}
      error={error}
      open={open}
      onOpenChange={setOpen}
    >
      {COMMON_TIMEZONES.map((tz) => (
        <LineItemButton
          key={tz}
          title={tz.replace(/_/g, " ")}
          sizePreset="main-ui"
          variant="body"
          state={current === tz ? "selected" : "empty"}
          onClick={() => {
            setOpen(false);
            if (tz !== current) void save(tz);
          }}
        />
      ))}
      <LineItemButton
        icon={SvgEdit}
        title="Other…"
        sizePreset="main-ui"
        variant="body"
        state="empty"
        onClick={() => {
          setOpen(false);
          setCustom(true);
        }}
      />
    </SelectRow>
  );
}

const LANDING_OPTIONS: {
  value: DefaultLanding;
  title: string;
  icon: typeof SvgBook;
}[] = [
  { value: "wiki_home", title: "Wiki Home", icon: SvgBook },
  { value: "recent", title: "Recently Edited", icon: SvgHistory },
  { value: "last_viewed", title: "Last Viewed Page", icon: SvgClock },
];

function LandingPageRow({
  settings,
  updateSettings,
}: {
  settings: UserSettings;
  updateSettings: UpdateSettings;
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active =
    LANDING_OPTIONS.find((o) => o.value === settings.default_landing) ??
    LANDING_OPTIONS[0];

  async function pick(value: DefaultLanding) {
    setOpen(false);
    if (value === settings.default_landing) return;
    setError(null);
    try {
      await updateSettings({ default_landing: value });
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    }
  }

  return (
    <SelectRow
      title="Landing Page"
      description="Default page to land on when the app opens."
      label={active.title}
      icon={active.icon}
      error={error}
      open={open}
      onOpenChange={setOpen}
    >
      {LANDING_OPTIONS.map((o) => (
        <LineItemButton
          key={o.value}
          icon={o.icon}
          title={o.title}
          sizePreset="main-ui"
          variant="body"
          state={settings.default_landing === o.value ? "selected" : "empty"}
          onClick={() => void pick(o.value)}
        />
      ))}
    </SelectRow>
  );
}

function SwitchRow({
  title,
  description,
  settingsKey,
  settings,
  updateSettings,
}: {
  title: string;
  description: string;
  settingsKey: "notify_comment_email" | "notify_update_warning_email";
  settings: UserSettings;
  updateSettings: UpdateSettings;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle(next: boolean) {
    setBusy(true);
    setError(null);
    try {
      await updateSettings({ [settingsKey]: next });
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex w-full flex-col gap-1">
      <InputHorizontal center title={title} description={description}>
        <div className="flex w-full max-w-[240px] justify-end">
          <Switch
            checked={settings[settingsKey]}
            disabled={busy}
            onCheckedChange={(next) => void toggle(next)}
          />
        </div>
      </InputHorizontal>
      {error && <InputErrorText type="error">{error}</InputErrorText>}
    </div>
  );
}

function ChangePasswordRow() {
  const [openForm, setOpenForm] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  function reset() {
    setCurrent("");
    setNext("");
    setConfirm("");
    setError(null);
  }

  async function submit() {
    if (next.length < 8) {
      setError("new password must be at least 8 characters");
      return;
    }
    if (next !== confirm) {
      setError("passwords do not match");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await apiFetch<void>("/user/password", {
        method: "PUT",
        body: JSON.stringify({
          current_password: current,
          new_password: next,
        }),
      });
      reset();
      setOpenForm(false);
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to change password");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex w-full flex-col gap-2">
      <InputHorizontal
        center
        title="Password"
        description="Manage your account password."
      >
        <div className="flex w-full max-w-[240px] justify-end">
          <Button
            type="button"
            icon={SvgLock}
            prominence="secondary"
            onClick={() => {
              setSaved(false);
              setOpenForm((v) => !v);
              if (openForm) reset();
            }}
          >
            Change Password
          </Button>
        </div>
      </InputHorizontal>
      {saved && !openForm && (
        <Text font="secondary-body" color="status-success-05">
          Password changed.
        </Text>
      )}
      {openForm && (
        <div className="flex w-full max-w-[360px] flex-col gap-2 self-end">
          <InputTypeIn
            type="password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            placeholder="Current password"
          />
          <InputTypeIn
            type="password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            placeholder="New password (min 8 characters)"
          />
          <InputTypeIn
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder="Confirm new password"
          />
          {error && <InputErrorText type="error">{error}</InputErrorText>}
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              prominence="secondary"
              disabled={busy}
              onClick={() => {
                reset();
                setOpenForm(false);
              }}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="action"
              disabled={busy || !current || !next || !confirm}
              onClick={() => void submit()}
            >
              {busy ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
