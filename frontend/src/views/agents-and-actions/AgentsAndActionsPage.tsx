"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";

import { ConnectOnyxCraft } from "@/components/agents/ConnectOnyxCraft";
import { SetupWizard } from "@/components/agents/SetupWizard";
import { ToolCard } from "@/components/agents/ToolCard";
import { Button } from "@onyx-ai/opal/components";
import { SvgActions } from "@onyx-ai/opal/icons";
import { SettingsLayouts } from "@onyx-ai/opal/layouts";
import { useConfirm } from "@/components/common/ConfirmDialog";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
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
import { useCraftConnect } from "@/lib/craft";
import { type ProbeResult } from "@/lib/launchers";

export default function AgentsAndActionsPage() {
  const { user, loading } = useRequireAuth();

  if (loading || !user) return <LoadingSpinner center />;

  return (
    <SettingsLayouts.Root width="lg">
      <SettingsLayouts.Header
        icon={SvgActions}
        title="Agents & Actions"
        description="Connect agents to read and update your wiki."
        divider
      />
      <SettingsLayouts.Body>
        <EndpointBlock />
        <TokenManager />
        <ClientConfigHelp />
        <CodingToolsSection />
        <OnyxCraftSection />
      </SettingsLayouts.Body>
    </SettingsLayouts.Root>
  );
}

// --------------------------------------------------------------------------- //
// Onyx Craft connection                                                       //
// --------------------------------------------------------------------------- //

function OnyxCraftSection() {
  // Hidden entirely until an admin configures the Onyx connection (the
  // status endpoint 404s while dark, surfacing as an SWR error).
  const { status, error } = useCraftConnect();
  if (error || !status) return null;

  return (
    <section className="mb-4 rounded-(--border-radius-08) border border-(--border-01) bg-(--background-tint-00) p-4">
      <h2 className="m-0 mb-1 text-base">Onyx Craft</h2>
      <p className="m-0 mb-3 text-[13px] text-(--text-03)">
        Connect your Onyx account to launch Craft builds from any wiki page with
        Run Agent. The build runs as you, with your knowledge and model access.
      </p>
      <ConnectOnyxCraft />
    </section>
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
    <section className="mb-4 rounded-(--border-radius-08) border border-(--border-01) bg-(--background-tint-00) p-4">
      <div className="mb-1.5 text-[13px] text-(--text-03)">MCP server URL</div>
      <div className="flex items-center gap-2">
        <code className="flex-1 overflow-x-auto rounded-(--border-radius-04) bg-(--background-tint-02) px-[10px] py-2 font-mono text-xs text-(--text-05)">
          {endpoint || "—"}
        </code>
        <CopyButton text={endpoint} />
      </div>
      <div className="mt-2 text-xs text-(--text-03)">
        Send the API key in the{" "}
        <code className="rounded-(--border-radius-04) bg-(--background-tint-02) px-1 py-px font-mono text-xs">
          Authorization
        </code>{" "}
        header as{" "}
        <code className="rounded-(--border-radius-04) bg-(--background-tint-02) px-1 py-px font-mono text-xs">
          Bearer mcp_…
        </code>
        .
      </div>
    </section>
  );
}

function TokenManager() {
  const { tokens, error, isLoading, refresh } = useTokens();
  const [showCreate, setShowCreate] = useState(false);
  const [reveal, setReveal] = useState<CreatedToken | null>(null);

  return (
    <section className="mb-4 rounded-(--border-radius-08) border border-(--border-01) bg-(--background-tint-00) p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="m-0 text-base">API keys</h2>
        <Button
          onClick={() => setShowCreate(true)}
          disabled={showCreate || reveal !== null}
        >
          Generate API key
        </Button>
      </div>

      {error && (
        <div className="mb-2 rounded-(--border-radius-04) bg-(--status-error-01) p-[10px] text-[13px] text-(--status-text-error-05)">
          {error.message || "Failed to load keys."}
        </div>
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
        <p className="text-sm text-(--text-03)">
          No keys yet — generate one above.
        </p>
      )}

      {tokens.length > 0 && (
        <ul className="m-0 list-none p-0">
          {tokens.map((t) => (
            <TokenRow key={t.id} token={t} onRevoked={() => void refresh()} />
          ))}
        </ul>
      )}
    </section>
  );
}

interface TokenRowProps {
  token: TokenSummary;
  onRevoked: () => void;
}

function TokenRow({ token, onRevoked }: TokenRowProps) {
  const [busy, setBusy] = useState(false);
  const confirmDialog = useConfirm();

  async function onRevoke() {
    if (
      !(await confirmDialog({
        title: `Revoke "${token.name}"?`,
        body: "Any agent using this key will stop working.",
        confirmLabel: "Revoke",
      }))
    )
      return;
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
    <li className="mt-2 flex items-center gap-3 rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-00) px-3 py-[10px]">
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-(--text-05)">{token.name}</div>
        <div className="mt-0.5 text-xs text-(--text-03)">
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

interface CreateFormProps {
  onCancel: () => void;
  onCreated: (t: CreatedToken) => void;
}

function CreateForm({ onCancel, onCreated }: CreateFormProps) {
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
      className="mb-3 rounded-(--border-radius-04) border border-(--border-01) bg-(--background-tint-01) p-3"
    >
      <label className="text-[13px] text-(--text-04)">
        Agent name
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Claude Code, Cursor, Codex"
          className="mt-1 box-border w-full rounded-(--border-radius-04) border border-(--border-01) px-[10px] py-2 text-sm"
          maxLength={80}
        />
        <div className="mt-1.5 text-xs text-(--text-03)">
          Appears next to this agent&apos;s reads, writes, and commits on the
          wiki. Pick something you&apos;ll recognize — you can have several
          agents per user.
        </div>
      </label>
      {err && (
        <div className="mt-[10px] mb-2 rounded-(--border-radius-04) bg-(--status-error-01) p-[10px] text-[13px] text-(--status-text-error-05)">
          {err}
        </div>
      )}
      <div className="mt-3 flex gap-2">
        <Button type="submit" disabled={busy || !name.trim()}>
          {busy ? "Creating…" : "Create"}
        </Button>
        <Button type="button" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

interface RevealOnceProps {
  token: CreatedToken;
  onClose: () => void;
}

function RevealOnce({ token, onClose }: RevealOnceProps) {
  return (
    <div className="mb-3 rounded-(--border-radius-04) border border-(--status-warning-02) bg-(--status-warning-01) p-[14px]">
      <div className="text-sm font-semibold text-(--status-text-warning-05)">
        Copy your key now — this is the only time it&apos;ll be shown.
      </div>
      <div className="mt-[10px] flex items-center gap-2">
        <code className="flex-1 overflow-x-auto rounded-(--border-radius-04) bg-(--background-tint-02) px-[10px] py-2 font-mono text-xs text-(--text-05)">
          {token.token}
        </code>
        <CopyButton text={token.token} />
      </div>
      <div className="mt-2 text-xs text-(--status-text-warning-05)">
        If you lose it, revoke this key and generate a new one.
      </div>
      <div className="mt-3">
        <Button onClick={onClose}>I&apos;ve saved it</Button>
      </div>
    </div>
  );
}

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
    <section className="mb-4 rounded-(--border-radius-08) border border-(--border-01) bg-(--background-tint-00) p-4">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex cursor-pointer items-center gap-1.5 border-none bg-transparent p-0 text-sm font-medium text-(--text-05)"
      >
        <span className="text-xs text-(--text-03)">{open ? "▾" : "▸"}</span>
        How to wire this into Claude Code, Cursor, or Codex
      </button>
      {open && (
        <div className="mt-3">
          <div className="mb-1.5 text-[13px] text-(--text-04)">
            Sample{" "}
            <code className="rounded-(--border-radius-04) bg-(--background-tint-02) px-1 py-px font-mono text-xs">
              mcp_servers.json
            </code>{" "}
            entry — replace the placeholder with your generated key:
          </div>
          <pre className="max-w-full flex-1 overflow-x-auto rounded-(--border-radius-04) bg-(--background-tint-02) p-3 px-[10px] py-2 font-mono text-xs whitespace-pre text-(--text-05)">
            {claudeCodeSnippet}
          </pre>
        </div>
      )}
    </section>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // Some browsers block the Clipboard API outside HTTPS — show feedback
      // anyway so the user knows to copy manually.
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

function CodingToolsSection() {
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
    if (node) node.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setWizardOpen(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [wizardOpen]);

  return (
    <section className="mb-4 rounded-(--border-radius-08) border border-(--border-01) bg-(--background-tint-00) p-4">
      <div className="mb-1 flex items-center justify-between">
        <h2 className="m-0 text-base">Coding tools</h2>
        <Button onClick={() => setWizardOpen(true)}>Set up tools</Button>
      </div>

      <p className="m-0 mb-3 text-[13px] text-(--text-03)">
        Launch Claude Code or Codex directly from the wiki. Install the launcher
        once, then start a session from any page with Run Agent.
      </p>

      <div className="mb-3 text-[13px] text-(--text-03)">
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
          className="fixed inset-0 z-[100] flex items-center justify-center bg-(--mask-03)"
        >
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-label="Set up launcher"
            tabIndex={-1}
            className="max-h-[90vh] w-[min(560px,92vw)] overflow-y-auto rounded-(--border-radius-12) bg-(--background-tint-00) p-[22px] shadow-(--shadow-modal)"
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
