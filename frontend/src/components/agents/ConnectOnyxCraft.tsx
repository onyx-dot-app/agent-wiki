"use client";

import { useState } from "react";

import { Button } from "@onyx-ai/opal/components";
import { ApiError } from "@/lib/api";
import { connectCraft, disconnectCraft, useCraftConnect } from "@/lib/craft";

interface Props {
  /** Fires after a successful connect — e.g. to dismiss a launch-panel modal. */
  onConnected?: () => void;
}

function connectErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) {
      return "Onyx rejected that token. Double-check you copied the whole PAT.";
    }
    if (err.status === 502) {
      return "Couldn't reach Onyx. Try again in a moment.";
    }
    return err.message;
  }
  return "Failed to connect.";
}

/**
 * Connect / disconnect the current user's Onyx account via a pasted PAT.
 * Renders nothing while the feature is dark (the status endpoint 404s),
 * so it can be dropped into any surface unconditionally.
 */
export function ConnectOnyxCraft({ onConnected }: Props) {
  const { status, error, isUnavailable, isLoading, refresh } =
    useCraftConnect();
  const [pat, setPat] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 404 (Craft not configured by an admin) → feature dark; show nothing.
  if (isUnavailable) return null;
  if (isLoading) return null;
  // A non-dark error (500/timeout) surfaces rather than silently hiding.
  if (error || !status) {
    return (
      <div className="rounded-(--border-radius-04) bg-(--status-error-01) p-[10px] text-[13px] text-(--status-text-error-05)">
        Couldn&apos;t load your Onyx connection. Try again in a moment.
      </div>
    );
  }

  async function onConnect() {
    const trimmed = pat.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setErr(null);
    try {
      await connectCraft(trimmed);
      setPat("");
      await refresh();
      onConnected?.();
    } catch (caught) {
      setErr(connectErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function onDisconnect() {
    setBusy(true);
    setErr(null);
    try {
      await disconnectCraft();
      await refresh();
    } catch (caught) {
      setErr(
        caught instanceof ApiError ? caught.message : "Failed to disconnect.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (status.connected) {
    return (
      <div className="flex items-center gap-3 rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-3 py-[10px]">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-(--status-text-success-05)">
            ✓ Connected to Onyx
          </div>
          <div className="mt-0.5 truncate text-xs text-(--text-03)">
            {status.onyx_user_email ?? "your account"}
            {status.token_hint ? ` · ${status.token_hint}` : ""}
          </div>
        </div>
        <Button
          type="button"
          size="sm"
          variant="danger"
          onClick={onDisconnect}
          disabled={busy}
        >
          {busy ? "…" : "Disconnect"}
        </Button>
      </div>
    );
  }

  // Scheme-guard the admin-set URL before it becomes an href — a javascript:
  // value would otherwise execute on click (stored XSS).
  const patHref =
    status.onyx_base_url && /^https?:\/\//i.test(status.onyx_base_url)
      ? `${status.onyx_base_url}/settings`
      : undefined;

  return (
    // Not a <form>: this drops into surfaces that are already inside a form
    // (the Run Agent panel), and a nested form submits the outer one — which
    // reloads the page and drops the connect. Submit via button + Enter key.
    <div className="rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-01) p-3">
      <label className="text-[13px] text-(--text-04)">
        Onyx Personal Access Token
        <input
          type="password"
          autoComplete="off"
          value={pat}
          onChange={(e) => setPat(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void onConnect();
            }
          }}
          placeholder="onyx_pat_…"
          className="mt-1 box-border w-full rounded-(--border-radius-04) border border-(--border-01) px-[10px] py-2 font-mono text-sm"
          maxLength={1024}
        />
      </label>
      <div className="mt-1.5 text-xs text-(--text-03)">
        Craft runs as you, so it uses your Onyx knowledge and model access. Mint
        a token in{" "}
        {patHref ? (
          <a
            href={patHref}
            target="_blank"
            rel="noopener noreferrer"
            className="text-(--action-link-05) underline"
          >
            Onyx → Settings → Accounts &amp; Access
          </a>
        ) : (
          "Onyx → Settings → Accounts & Access"
        )}
        , then paste it here.
      </div>
      {err && (
        <div className="mt-[10px] mb-1 rounded-(--border-radius-04) bg-(--status-error-01) p-[10px] text-[13px] text-(--status-text-error-05)">
          {err}
        </div>
      )}
      <div className="mt-3">
        <Button
          type="button"
          variant="action"
          onClick={() => void onConnect()}
          disabled={busy || !pat.trim()}
        >
          {busy ? "Connecting…" : "Connect Onyx"}
        </Button>
      </div>
    </div>
  );
}
