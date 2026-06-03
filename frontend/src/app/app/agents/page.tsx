"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";

import { SetupWizard } from "@/components/agents/SetupWizard";
import { ToolCard } from "@/components/agents/ToolCard";
import { Button } from "@onyx-ai/opal/components";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { PageHeader } from "@/components/common/PageHeader";
import { ApiError } from "@/lib/api";
import {
  createToken,
  mcpEndpointUrl,
  revokeToken,
  useTokens,
  type CreatedToken,
  type TokenSummary,
} from "@/lib/agents";
import { apiFetch } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { type ProbeResult } from "@/lib/launchers";
import { useIsMobile } from "@/lib/viewport";

export default function AgentsPage() {
  const { user, loading } = useRequireAuth();
  const isMobile = useIsMobile();

  if (loading || !user)
    return (
      <main className={isMobile ? "p-4" : "p-8"}>
        <LoadingSpinner center />
      </main>
    );

  return (
    <main
      className={`max-w-[880px] ${isMobile ? "px-3 py-4" : "px-8 py-6"}`}
    >
        <PageHeader
          title="Agents"
          description="Give your agents the ability to read and update this wiki. Generate a personal API key below, then drop it into your coding agent's MCP configuration. Each key's name becomes that agent's identity — it shows up next to its activity on wiki pages and in commit history."
        />

        <EndpointBlock />
        <TokenManager />
        <ClientConfigHelp />
        <CodingToolsSection />
    </main>
  );
}

// --------------------------------------------------------------------------- //
// Endpoint                                                                    //
// --------------------------------------------------------------------------- //

function EndpointBlock() {
  const [endpoint, setEndpoint] = useState("");

  useEffect(() => {
    setEndpoint(mcpEndpointUrl());
  }, []);

  return (
    <section className="p-4 border border-(--border-01) rounded-(--border-radius-08) bg-(--background-tint-00) mb-4">
      <div className="text-[13px] text-(--text-03) mb-1.5">
        MCP server URL
      </div>
      <div className="flex gap-2 items-center">
        <code className="flex-1 py-2 px-[10px] bg-(--background-tint-02) rounded-(--border-radius-04) font-mono text-xs text-(--text-05) overflow-x-auto">{endpoint || "—"}</code>
        <CopyButton text={endpoint} />
      </div>
      <div className="text-xs text-(--text-03) mt-2">
        Send the API key in the <code className="py-px px-1 bg-(--background-tint-02) rounded-(--border-radius-04) font-mono text-xs">Authorization</code>{" "}
        header as <code className="py-px px-1 bg-(--background-tint-02) rounded-(--border-radius-04) font-mono text-xs">Bearer mcp_…</code>.
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------- //
// Token list + create                                                         //
// --------------------------------------------------------------------------- //

function TokenManager() {
  const { tokens, error, isLoading, refresh } = useTokens();
  const [showCreate, setShowCreate] = useState(false);
  const [reveal, setReveal] = useState<CreatedToken | null>(null);

  return (
    <section className="p-4 border border-(--border-01) rounded-(--border-radius-08) bg-(--background-tint-00) mb-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="m-0 text-base">API keys</h2>
        <Button
          variant="action"
          onClick={() => setShowCreate(true)}
          disabled={showCreate || reveal !== null}
        >
          Generate API key
        </Button>
      </div>

      {error && (
        <div className="p-[10px] bg-(--status-error-01) text-(--status-text-error-05) rounded-(--border-radius-04) text-[13px] mb-2">{error.message || "Failed to load keys."}</div>
      )}

      {showCreate && (
        <CreateForm
          onCancel={() => setShowCreate(false)}
          onCreated={async (t) => {
            setShowCreate(false);
            setReveal(t);
            await refresh();
          }}
        />
      )}

      {reveal && <RevealOnce token={reveal} onClose={() => setReveal(null)} />}

      {isLoading && tokens.length === 0 && !error && <LoadingSpinner />}

      {!isLoading && tokens.length === 0 && (
        <p className="text-(--text-03) text-sm">
          No keys yet — generate one above.
        </p>
      )}

      {tokens.length > 0 && (
        <ul className="list-none p-0 m-0">
          {tokens.map((t) => (
            <TokenRow key={t.id} token={t} onRevoked={() => void refresh()} />
          ))}
        </ul>
      )}
    </section>
  );
}

function TokenRow({
  token,
  onRevoked,
}: {
  token: TokenSummary;
  onRevoked: () => void;
}) {
  const [busy, setBusy] = useState(false);

  async function onRevoke() {
    if (
      !confirm(
        `Revoke "${token.name}"? Any agent using this key will stop working.`,
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      await revokeToken(token.id);
      onRevoked();
    } catch (err) {
      alert(err instanceof ApiError ? err.message : "Failed to revoke");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="flex items-center gap-3 py-[10px] px-3 border border-(--border-01) rounded-(--border-radius-04) mt-2 bg-(--background-tint-00)">
      <div className="flex-1 min-w-0">
        <div className="font-medium text-sm text-(--text-05)">
          {token.name}
        </div>
        <div className="text-xs text-(--text-03) mt-0.5">
          Created {token.created_at}
          {token.last_used_at
            ? ` · last used ${token.last_used_at}`
            : " · never used"}
        </div>
      </div>
      <Button size="sm" variant="danger" onClick={onRevoke} disabled={busy}>
        {busy ? "Revoking…" : "Revoke"}
      </Button>
    </li>
  );
}

function CreateForm({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: (t: CreatedToken) => void;
}) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const t = await createToken(name.trim());
      onCreated(t);
    } catch (caught) {
      setErr(caught instanceof ApiError ? caught.message : "Failed to create");
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={onSubmit}
      className="p-3 bg-(--background-tint-01) border border-(--border-01) rounded-(--border-radius-04) mb-3"
    >
      <label className="text-[13px] text-(--text-04)">
        Agent name
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Claude Code, Cursor, Codex"
          className="w-full box-border py-2 px-[10px] border border-(--border-01) rounded-(--border-radius-04) text-sm mt-1"
          maxLength={80}
        />
        <div className="text-xs text-(--text-03) mt-1.5">
          Appears next to this agent&apos;s reads, writes, and commits on the
          wiki. Pick something you&apos;ll recognize — you can have several
          agents per user.
        </div>
      </label>
      {err && <div className="p-[10px] bg-(--status-error-01) text-(--status-text-error-05) rounded-(--border-radius-04) text-[13px] mb-2 mt-[10px]">{err}</div>}
      <div className="flex gap-2 mt-3">
        <Button type="submit" variant="action" disabled={busy || !name.trim()}>
          {busy ? "Creating…" : "Create"}
        </Button>
        <Button type="button" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function RevealOnce({
  token,
  onClose,
}: {
  token: CreatedToken;
  onClose: () => void;
}) {
  return (
    <div className="p-[14px] bg-(--status-warning-01) border border-(--status-warning-02) rounded-(--border-radius-04) mb-3">
      <div className="font-semibold text-(--status-text-warning-05) text-sm">
        Copy your key now — this is the only time it&apos;ll be shown.
      </div>
      <div className="flex gap-2 items-center mt-[10px]">
        <code className="flex-1 py-2 px-[10px] bg-(--background-tint-02) rounded-(--border-radius-04) font-mono text-xs text-(--text-05) overflow-x-auto">{token.token}</code>
        <CopyButton text={token.token} />
      </div>
      <div className="text-xs text-(--status-text-warning-05) mt-2">
        If you lose it, revoke this key and generate a new one.
      </div>
      <div className="mt-3">
        <Button onClick={onClose}>I&apos;ve saved it</Button>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Client-config help                                                          //
// --------------------------------------------------------------------------- //

function ClientConfigHelp() {
  const [open, setOpen] = useState(false);
  const [endpoint, setEndpoint] = useState("");

  useEffect(() => {
    setEndpoint(mcpEndpointUrl());
  }, []);

  const claudeCodeSnippet = JSON.stringify(
    {
      mcpServers: {
        "agent-wiki": {
          url: endpoint || "https://your-host/api/mcp",
          headers: { Authorization: "Bearer mcp_REPLACE_ME" },
        },
      },
    },
    null,
    2,
  );

  return (
    <section className="p-4 border border-(--border-01) rounded-(--border-radius-08) bg-(--background-tint-00) mb-4">
      <button
        onClick={() => setOpen((v) => !v)}
        className="bg-transparent border-none p-0 cursor-pointer text-sm font-medium text-(--text-05) flex items-center gap-1.5"
      >
        <span className="text-xs text-(--text-03)">
          {open ? "▾" : "▸"}
        </span>
        How to wire this into Claude Code, Cursor, or Codex
      </button>
      {open && (
        <div className="mt-3">
          <div className="text-[13px] text-(--text-04) mb-1.5">
            Sample <code className="py-px px-1 bg-(--background-tint-02) rounded-(--border-radius-04) font-mono text-xs">mcp_servers.json</code> entry —
            replace the placeholder with your generated key:
          </div>
          <pre className="flex-1 py-2 px-[10px] bg-(--background-tint-02) rounded-(--border-radius-04) font-mono text-xs text-(--text-05) overflow-x-auto p-3 whitespace-pre max-w-full">
            {claudeCodeSnippet}
          </pre>
        </div>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- //
// Reusable bits                                                               //
// --------------------------------------------------------------------------- //

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // Some browsers block clipboard outside HTTPS — show feedback
      // anyway, the user can fall back to manual select-copy.
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    }
  }

  return (
    <Button size="sm" onClick={copy} disabled={!text}>
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

// --------------------------------------------------------------------------- //
// Coding tools section                          //
// --------------------------------------------------------------------------- //

function CodingToolsSection() {
  // SWR-driven install state — HTTP only, no iframe. Backend records
  // helper presence via agent_session.machine_id; FE just polls the
  // record. iframe probe stays in InstallHelperPane behind the
  // "I've installed it" button for the first-launch case where the
  // user has installed but never run an agent yet.
  const { data: helperInstalled } = useSWR<{
    installed: boolean;
    machine_id: string | null;
  }>("/launchers/helper-installed", {
    refreshInterval: 2000,
    revalidateOnFocus: true,
  });
  const probe: ProbeResult | null = helperInstalled
    ? {
        acked: helperInstalled.installed,
        helperPort: null,
        machineId: helperInstalled.machine_id,
      }
    : null;
  const [wizardOpen, setWizardOpen] = useState(false);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!wizardOpen) return;
    const node = dialogRef.current;
    if (node) {
      node.focus();
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setWizardOpen(false);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [wizardOpen]);

  return (
    <section className="p-4 border border-(--border-01) rounded-(--border-radius-08) bg-(--background-tint-00) mb-4">
      <div className="flex justify-between items-center mb-1">
        <h2 className="m-0 text-base">Coding tools</h2>
        <Button variant="action" onClick={() => setWizardOpen(true)}>
          Set up tools
        </Button>
      </div>

      <p className="m-0 mb-3 text-[13px] text-(--text-03)">
        Launch Claude Code or Codex directly from the wiki. Install the launcher
        once, then start a session from any page with Run Agent.
      </p>

      {/* Surface helper-install status. */}
      <div className="text-[13px] text-(--text-03) mb-3">
        Launcher:{" "}
        {probe === null ? (
          "checking…"
        ) : probe.acked ? (
          <span className="text-(--status-text-success-05)">
            ✓ detected on this machine
          </span>
        ) : (
          <span className="text-(--status-text-warning-05)">
            ⚠ not detected —{" "}
            <a
              href="/api/installer/app"
              className="text-(--status-text-warning-05) underline"
            >
              download AgentWikiLauncher.app
            </a>
          </span>
        )}
      </div>

      {wizardOpen && (
        <div
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setWizardOpen(false);
          }}
          className="fixed inset-0 bg-(--mask-03) flex items-center justify-center z-[100]"
        >
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-label="Set up launcher"
            tabIndex={-1}
            className="bg-(--background-tint-00) rounded-(--border-radius-12) p-[22px] w-[min(560px,92vw)] shadow-(--shadow-modal) max-h-[90vh] overflow-y-auto"
          >
            <SetupWizard
              onDone={() => setWizardOpen(false)}
              onCancel={() => setWizardOpen(false)}
            />
          </div>
        </div>
      )}
    </section>
  );
}
