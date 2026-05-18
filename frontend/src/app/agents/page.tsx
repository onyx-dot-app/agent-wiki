"use client";

import { useEffect, useState } from "react";

import { SetupWizard } from "@/components/agents/SetupWizard";
import { ToolCard } from "@/components/agents/ToolCard";
import { AppShell } from "@/components/common/AppShell";
import { Button } from "@/components/common/Button";
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
import {
  probeHelper,
  useLauncherCatalog,
  type ProbeResult,
} from "@/lib/launchers";
import { color, radius, shadow } from "@/lib/theme";
import { useIsMobile } from "@/lib/viewport";

export default function AgentsPage() {
  const { user, loading } = useRequireAuth();
  const isMobile = useIsMobile();

  if (loading || !user)
    return <main style={{ padding: isMobile ? 16 : 32 }}>Loading…</main>;

  return (
    <AppShell>
      <main
        style={{ padding: isMobile ? "16px 12px" : "24px 32px", maxWidth: 880 }}
      >
        <PageHeader
          title="Agents"
          description="Give your agents the ability to read and update this wiki. Generate a personal API key below, then drop it into your coding agent's MCP configuration. Each key's name becomes that agent's identity — it shows up next to its activity on wiki pages and in commit history."
        />

        <CodingToolsSection />
        <EndpointBlock />
        <TokenManager />
        <ClientConfigHelp />
        {user.is_admin && <OnyxConnection />}
      </main>
    </AppShell>
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
    <section style={card}>
      <div style={{ fontSize: 13, color: color.text.muted, marginBottom: 6 }}>
        MCP server URL
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <code style={codeBlock}>{endpoint || "—"}</code>
        <CopyButton text={endpoint} />
      </div>
      <div style={{ fontSize: 12, color: color.text.muted, marginTop: 8 }}>
        Send the API key in the <code style={inlineCode}>Authorization</code>{" "}
        header as <code style={inlineCode}>Bearer mcp_…</code>.
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
    <section style={card}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 12,
        }}
      >
        <h2 style={{ margin: 0, fontSize: 16 }}>API keys</h2>
        <Button
          variant="primary"
          onClick={() => setShowCreate(true)}
          disabled={showCreate || reveal !== null}
        >
          Generate API key
        </Button>
      </div>

      {error && (
        <div style={errorBanner}>{error.message || "Failed to load keys."}</div>
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

      {isLoading && tokens.length === 0 && !error && (
        <p style={{ color: color.text.muted, fontSize: 14 }}>Loading…</p>
      )}

      {!isLoading && tokens.length === 0 && (
        <p style={{ color: color.text.muted, fontSize: 14 }}>
          No keys yet — generate one above.
        </p>
      )}

      {tokens.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
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
    <li
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "10px 12px",
        border: `1px solid ${color.border.default}`,
        borderRadius: radius.sm,
        marginTop: 8,
        background: color.bg.page,
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{ fontWeight: 500, fontSize: 14, color: color.text.primary }}
        >
          {token.name}
        </div>
        <div style={{ fontSize: 12, color: color.text.muted, marginTop: 2 }}>
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
      style={{
        padding: 12,
        background: color.bg.panel,
        border: `1px solid ${color.border.default}`,
        borderRadius: radius.sm,
        marginBottom: 12,
      }}
    >
      <label style={{ fontSize: 13, color: color.text.secondary }}>
        Agent name
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Claude Code, Cursor, Codex"
          style={{ ...inputStyle, marginTop: 4 }}
          maxLength={80}
        />
        <div style={{ fontSize: 12, color: color.text.muted, marginTop: 6 }}>
          Appears next to this agent&apos;s reads, writes, and commits on the
          wiki. Pick something you&apos;ll recognize — you can have several
          agents per user.
        </div>
      </label>
      {err && <div style={{ ...errorBanner, marginTop: 10 }}>{err}</div>}
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <Button type="submit" variant="primary" disabled={busy || !name.trim()}>
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
    <div
      style={{
        padding: 14,
        background: color.state.warning.bg,
        border: `1px solid ${color.state.warning.border}`,
        borderRadius: radius.sm,
        marginBottom: 12,
      }}
    >
      <div
        style={{ fontWeight: 600, color: color.state.warning.fg, fontSize: 14 }}
      >
        Copy your key now — this is the only time it&apos;ll be shown.
      </div>
      <div
        style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10 }}
      >
        <code style={codeBlock}>{token.token}</code>
        <CopyButton text={token.token} />
      </div>
      <div
        style={{ fontSize: 12, color: color.state.warning.fg, marginTop: 8 }}
      >
        If you lose it, revoke this key and generate a new one.
      </div>
      <Button onClick={onClose} style={{ marginTop: 12 }}>
        I&apos;ve saved it
      </Button>
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
    <section style={card}>
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          background: "transparent",
          border: "none",
          padding: 0,
          cursor: "pointer",
          fontSize: 14,
          fontWeight: 500,
          color: color.text.primary,
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <span style={{ fontSize: 12, color: color.text.muted }}>
          {open ? "▾" : "▸"}
        </span>
        How to wire this into Claude Code, Cursor, or Codex
      </button>
      {open && (
        <div style={{ marginTop: 12 }}>
          <div
            style={{
              fontSize: 13,
              color: color.text.secondary,
              marginBottom: 6,
            }}
          >
            Sample <code style={inlineCode}>mcp_servers.json</code> entry —
            replace the placeholder with your generated key:
          </div>
          <pre
            style={{
              ...codeBlock,
              padding: 12,
              whiteSpace: "pre",
              overflowX: "auto",
              maxWidth: "100%",
            }}
          >
            {claudeCodeSnippet}
          </pre>
        </div>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- //
// Onyx connection (admin only)                                               //
// --------------------------------------------------------------------------- //

interface IngestSettings {
  api_key: string | null;
}

function OnyxConnection() {
  const [settings, setSettings] = useState<IngestSettings | null>(null);
  const [ingestUrl, setIngestUrl] = useState("");
  const [keyVisible, setKeyVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    setIngestUrl(`${window.location.origin}/api/wiki/ingest`);
    apiFetch<IngestSettings>("/admin/ingest")
      .then(setSettings)
      .catch((e) => setError(e instanceof Error ? e.message : "failed to load"));
  }, []);

  async function regenerate() {
    if (
      settings?.api_key &&
      !confirm("Regenerate the API key? The old key will stop working immediately.")
    )
      return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await apiFetch<{ api_key: string }>("/admin/ingest/regenerate-key", { method: "POST" });
      setSettings((prev) => (prev ? { ...prev, api_key: r.api_key } : prev));
      setKeyVisible(true);
      setNotice("New key generated. Copy it now — it will be masked after you leave this page.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to regenerate");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section style={card}>
      <h2 style={{ margin: "0 0 12px", fontSize: 16 }}>Onyx connection</h2>
      <div style={{ fontSize: 13, color: color.text.muted, marginBottom: 16 }}>
        Push indexed documents from Onyx into this wiki automatically. Copy the endpoint and API key
        into your Onyx environment variables.
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {/* Ingest URL */}
        <div>
          <div style={{ fontSize: 13, color: color.text.muted, marginBottom: 6 }}>Endpoint URL</div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <code style={codeBlock}>{ingestUrl || "—"}</code>
            <CopyButton text={ingestUrl} />
          </div>
        </div>

        {/* API key */}
        <div>
          <div style={{ fontSize: 13, color: color.text.muted, marginBottom: 6 }}>API key</div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <code style={codeBlock}>
              {settings === null
                ? "Loading…"
                : settings.api_key
                ? keyVisible
                  ? settings.api_key
                  : "••••••••••••••••••••••••••••••••"
                : "No key yet — click Regenerate"}
            </code>
            {settings?.api_key && keyVisible && <CopyButton text={settings.api_key} />}
            <Button size="sm" variant={settings?.api_key ? "secondary" : "primary"} disabled={busy} onClick={() => void regenerate()}>
              {busy ? "…" : "Regenerate"}
            </Button>
          </div>
        </div>
      </div>

      {notice && <div style={{ fontSize: 13, color: color.state.success.fg, marginTop: 10 }}>{notice}</div>}
      {error && <div style={{ fontSize: 13, color: color.state.danger.fg, marginTop: 10 }}>{error}</div>}
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

const card: React.CSSProperties = {
  padding: 16,
  border: `1px solid ${color.border.default}`,
  borderRadius: radius.md,
  background: color.bg.page,
  marginBottom: 16,
};

const codeBlock: React.CSSProperties = {
  flex: 1,
  padding: "8px 10px",
  background: color.bg.sunken,
  borderRadius: radius.xs,
  fontFamily: "ui-monospace, Menlo, monospace",
  fontSize: 12,
  color: color.text.primary,
  overflowX: "auto",
};

const inlineCode: React.CSSProperties = {
  padding: "1px 4px",
  background: color.bg.sunken,
  borderRadius: radius.xs,
  fontFamily: "ui-monospace, Menlo, monospace",
  fontSize: 12,
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  padding: "8px 10px",
  border: `1px solid ${color.border.default}`,
  borderRadius: radius.sm,
  fontSize: 14,
};

const errorBanner: React.CSSProperties = {
  padding: 10,
  background: color.state.danger.bg,
  color: color.state.danger.fg,
  borderRadius: radius.sm,
  fontSize: 13,
  marginBottom: 8,
};

// --------------------------------------------------------------------------- //
// Coding tools section                          //
// --------------------------------------------------------------------------- //

function CodingToolsSection() {
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const { launchers } = useLauncherCatalog({
    machineId: probe?.machineId ?? null,
  });
  const [wizardOpen, setWizardOpen] = useState(false);

  useEffect(() => {
    if (wizardOpen) return;
    let cancelled = false;
    void (async () => {
      const result = await probeHelper();
      if (!cancelled) setProbe(result);
    })();
    return () => {
      cancelled = true;
    };
  }, [wizardOpen]);

  return (
    <section style={card}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 12,
        }}
      >
        <h2 style={{ margin: 0, fontSize: 16 }}>Coding tools</h2>
        <Button variant="primary" onClick={() => setWizardOpen(true)}>
          Set up tools
        </Button>
      </div>

      {/* Surface helper-install status. */}
      <div style={{ fontSize: 13, color: color.text.muted, marginBottom: 12 }}>
        Launcher:{" "}
        {probe === null ? (
          "checking…"
        ) : probe.acked ? (
          <span style={{ color: color.state.success.fg }}>
            ✓ detected on this machine
          </span>
        ) : (
          <span style={{ color: color.state.warning.fg }}>
            ⚠ not detected — run{" "}
            <code style={inlineCode}>npm install -g @agentwiki/launcher</code>
          </span>
        )}
      </div>

      <ul
        style={{
          listStyle: "none",
          padding: 0,
          margin: 0,
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        {launchers.map((c) => (
          <li key={c.id}>
            <ToolCard
              id={c.id}
              name={c.name}
              tagline={c.tagline}
              iconUrl={c.icon_url}
              selected={false}
            />
          </li>
        ))}
      </ul>

      {wizardOpen && (
        <div
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setWizardOpen(false);
          }}
          style={{
            position: "fixed",
            inset: 0,
            background: color.overlay,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 100,
          }}
        >
          <div
            style={{
              background: color.bg.page,
              borderRadius: radius.lg,
              padding: 22,
              width: "min(560px, 92vw)",
              boxShadow: shadow.modal,
              maxHeight: "90vh",
              overflowY: "auto",
            }}
          >
            <SetupWizard
              catalog={launchers}
              onDone={() => setWizardOpen(false)}
              onCancel={() => setWizardOpen(false)}
            />
          </div>
        </div>
      )}
    </section>
  );
}
