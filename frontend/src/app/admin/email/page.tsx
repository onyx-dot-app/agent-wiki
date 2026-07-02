"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@onyx-ai/opal/components";
import { useConfirm } from "@/components/common/ConfirmDialog";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { BackLink, PageHeader } from "@/components/common/PageHeader";
import { RequireAdmin } from "@/components/RequireAdmin";
import { apiFetch } from "@/lib/api";
import { useIsMobile } from "@/lib/viewport";

interface EmailSmtpSettings {
  host: string;
  port: number;
  username: string;
  password_set: boolean;
  password_hint: string;
  from_address: string;
}

interface EmailTestResult {
  ok: boolean;
  detail: string;
}

export default function AdminEmailPage() {
  const isMobile = useIsMobile();
  return (
    <RequireAdmin>
      <main
        className="max-w-[720px]"
        style={{ padding: isMobile ? "16px 12px" : "24px 32px" }}
      >
        <BackLink />
        <PageHeader
          title="Outbound email"
          description="The SMTP account every email the wiki sends goes through — trigger notifications, verification links, and notification emails. Sending stays off until a host and from address are saved."
        />
        <EmailSmtpForm />
      </main>
    </RequireAdmin>
  );
}

function EmailSmtpForm() {
  const [settings, setSettings] = useState<EmailSmtpSettings | null>(null);
  const [host, setHost] = useState("");
  const [port, setPort] = useState("587");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [fromAddress, setFromAddress] = useState("");
  const [testTo, setTestTo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<EmailTestResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const confirmDialog = useConfirm();

  async function load() {
    try {
      const r = await apiFetch<EmailSmtpSettings>("/admin/email-smtp");
      setSettings(r);
      setHost(r.host);
      setPort(String(r.port));
      setUsername(r.username);
      setFromAddress(r.from_address);
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
    setSaved(null);
    setTestResult(null);
    try {
      const body: Record<string, unknown> = {
        host,
        port: Number(port) || 587,
        username,
        from_address: fromAddress,
      };
      if (password) body.password = password;
      const r = await apiFetch<EmailSmtpSettings>("/admin/email-smtp", {
        method: "PUT",
        body: JSON.stringify(body),
      });
      setSettings(r);
      setPassword("");
      setSaved("Saved.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to save");
    } finally {
      setSaving(false);
    }
  }

  async function clearPassword() {
    if (
      !(await confirmDialog({
        title: "Clear the SMTP password?",
        body: "Sending will fail until a new password is set (unless the relay allows unauthenticated mail).",
        confirmLabel: "Clear password",
      }))
    )
      return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const r = await apiFetch<EmailSmtpSettings>("/admin/email-smtp", {
        method: "PUT",
        body: JSON.stringify({
          host,
          port: Number(port) || 587,
          username,
          from_address: fromAddress,
          password: null,
        }),
      });
      setSettings(r);
      setPassword("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to clear");
    } finally {
      setSaving(false);
    }
  }

  async function sendTest() {
    setTesting(true);
    setError(null);
    setTestResult(null);
    try {
      const r = await apiFetch<EmailTestResult>("/admin/email-smtp/test", {
        method: "POST",
        body: JSON.stringify({ to: testTo }),
      });
      setTestResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "test failed");
    } finally {
      setTesting(false);
    }
  }

  if (!settings) return <LoadingSpinner />;

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <div className="flex gap-3">
        <label className="flex-1">
          <div className="mb-1 text-[13px] font-medium">SMTP host</div>
          <input
            value={host}
            onChange={(e) => setHost(e.target.value)}
            placeholder="smtp.gmail.com"
            required
            className={inputClass}
          />
        </label>
        <label className="w-[110px]">
          <div className="mb-1 text-[13px] font-medium">Port</div>
          <input
            value={port}
            onChange={(e) => setPort(e.target.value)}
            inputMode="numeric"
            placeholder="587"
            className={inputClass}
          />
        </label>
      </div>

      <label>
        <div className="mb-1 text-[13px] font-medium">Username</div>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="wiki@yourdomain.com"
          className={inputClass}
        />
      </label>

      <label>
        <div className="mb-1 flex items-center gap-[6px] text-[13px] font-medium">
          <span>Password</span>
          {settings.password_set && (
            <span className="font-mono text-xs font-normal text-(--text-03)">
              currently {settings.password_hint}
            </span>
          )}
          <span className="flex-1" />
          {settings.password_set && (
            <Button
              type="button"
              size="sm"
              variant="danger"
              onClick={() => void clearPassword()}
              disabled={saving}
            >
              Clear
            </Button>
          )}
        </div>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={
            settings.password_set ? "leave blank to keep" : "app password"
          }
          className={inputClass}
        />
      </label>

      <label>
        <div className="mb-1 text-[13px] font-medium">From address</div>
        <input
          value={fromAddress}
          onChange={(e) => setFromAddress(e.target.value)}
          placeholder="wiki@yourdomain.com"
          required
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

      <div className="mt-2 border-t border-(--border-01) pt-4">
        <div className="mb-1 text-[13px] font-medium">Send a test email</div>
        <div className="flex gap-3">
          <input
            value={testTo}
            onChange={(e) => setTestTo(e.target.value)}
            placeholder="leave blank to send to yourself"
            className={inputClass}
          />
          <Button
            type="button"
            variant="default"
            onClick={() => void sendTest()}
            disabled={testing || saving}
          >
            {testing ? "Sending…" : "Send test"}
          </Button>
        </div>
        {testResult && (
          <div
            className={
              testResult.ok
                ? "mt-2 text-(--status-text-success-05)"
                : "mt-2 text-(--status-text-error-05)"
            }
          >
            {testResult.detail}
          </div>
        )}
      </div>
    </form>
  );
}

const inputClass =
  "w-full py-2 px-[10px] box-border border border-(--border-01) rounded-(--border-radius-04) text-sm";
