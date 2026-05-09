"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/common/AppShell";
import { ApiError } from "@/lib/api";
import {
  createToken,
  mcpEndpointUrl,
  revokeToken,
  useTokens,
  type CreatedToken,
  type TokenSummary,
} from "@/lib/agents";
import { useRequireAuth } from "@/lib/auth";

export default function AgentsPage() {
  const { user, loading } = useRequireAuth();

  if (loading || !user) return <main style={{ padding: 32 }}>Loading…</main>;

  return (
    <AppShell>
      <main style={{ padding: "24px 32px", maxWidth: 880 }}>
        <header style={{ marginBottom: 20 }}>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>Agents</h1>
          <p style={{ color: "#555", margin: "6px 0 0", fontSize: 14, lineHeight: 1.5 }}>
            Give your agents the ability to read and update this wiki. Generate a
            personal API key below, then drop it into your coding agent&apos;s MCP
            configuration.
          </p>
        </header>

        <EndpointBlock />
        <TokenManager />
        <ClientConfigHelp />
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
      <div style={{ fontSize: 13, color: "#666", marginBottom: 6 }}>MCP server URL</div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <code style={codeBlock}>{endpoint || "—"}</code>
        <CopyButton text={endpoint} />
      </div>
      <div style={{ fontSize: 12, color: "#777", marginTop: 8 }}>
        Send the API key in the <code style={inlineCode}>Authorization</code> header as{" "}
        <code style={inlineCode}>Bearer mcp_…</code>.
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
        <button
          onClick={() => setShowCreate(true)}
          style={primaryBtn}
          disabled={showCreate || reveal !== null}
        >
          Generate API key
        </button>
      </div>

      {error && (
        <div style={errorBanner}>
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

      {reveal && (
        <RevealOnce token={reveal} onClose={() => setReveal(null)} />
      )}

      {isLoading && tokens.length === 0 && !error && (
        <p style={{ color: "#888", fontSize: 14 }}>Loading…</p>
      )}

      {!isLoading && tokens.length === 0 && (
        <p style={{ color: "#888", fontSize: 14 }}>
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

function TokenRow({ token, onRevoked }: { token: TokenSummary; onRevoked: () => void }) {
  const [busy, setBusy] = useState(false);

  async function onRevoke() {
    if (!confirm(`Revoke "${token.name}"? Any agent using this key will stop working.`)) {
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
        border: "1px solid #e5e7eb",
        borderRadius: 6,
        marginTop: 8,
        background: "white",
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 500, fontSize: 14, color: "#111" }}>{token.name}</div>
        <div style={{ fontSize: 12, color: "#6b7280", marginTop: 2 }}>
          Created {token.created_at}
          {token.last_used_at ? ` · last used ${token.last_used_at}` : " · never used"}
        </div>
      </div>
      <button onClick={onRevoke} disabled={busy} style={dangerBtn}>
        {busy ? "Revoking…" : "Revoke"}
      </button>
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
        background: "#f9fafb",
        border: "1px solid #e5e7eb",
        borderRadius: 6,
        marginBottom: 12,
      }}
    >
      <label style={{ fontSize: 13, color: "#374151" }}>
        Name
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. claude-code laptop"
          style={{ ...inputStyle, marginTop: 4 }}
          maxLength={80}
        />
      </label>
      {err && <div style={{ ...errorBanner, marginTop: 10 }}>{err}</div>}
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button type="submit" disabled={busy || !name.trim()} style={primaryBtn}>
          {busy ? "Creating…" : "Create"}
        </button>
        <button type="button" onClick={onCancel} disabled={busy} style={secondaryBtn}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function RevealOnce({ token, onClose }: { token: CreatedToken; onClose: () => void }) {
  return (
    <div
      style={{
        padding: 14,
        background: "#fffbeb",
        border: "1px solid #fcd34d",
        borderRadius: 6,
        marginBottom: 12,
      }}
    >
      <div style={{ fontWeight: 600, color: "#78350f", fontSize: 14 }}>
        Copy your key now — this is the only time it&apos;ll be shown.
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10 }}>
        <code style={codeBlock}>{token.token}</code>
        <CopyButton text={token.token} />
      </div>
      <div style={{ fontSize: 12, color: "#92400e", marginTop: 8 }}>
        If you lose it, revoke this key and generate a new one.
      </div>
      <button onClick={onClose} style={{ ...secondaryBtn, marginTop: 12 }}>
        I&apos;ve saved it
      </button>
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
          color: "#1f2937",
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
      >
        <span style={{ fontSize: 12, color: "#6b7280" }}>{open ? "▾" : "▸"}</span>
        How to wire this into Claude Code, Cursor, or Codex
      </button>
      {open && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 13, color: "#374151", marginBottom: 6 }}>
            Sample <code style={inlineCode}>mcp_servers.json</code> entry — replace
            the placeholder with your generated key:
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
    <button onClick={copy} style={secondaryBtn} disabled={!text}>
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

const card: React.CSSProperties = {
  padding: 16,
  border: "1px solid #e5e7eb",
  borderRadius: 8,
  background: "white",
  marginBottom: 16,
};

const codeBlock: React.CSSProperties = {
  flex: 1,
  padding: "8px 10px",
  background: "#f3f4f6",
  borderRadius: 4,
  fontFamily: "ui-monospace, Menlo, monospace",
  fontSize: 12,
  color: "#111",
  overflowX: "auto",
};

const inlineCode: React.CSSProperties = {
  padding: "1px 4px",
  background: "#f3f4f6",
  borderRadius: 3,
  fontFamily: "ui-monospace, Menlo, monospace",
  fontSize: 12,
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  border: "1px solid #d1d5db",
  borderRadius: 4,
  fontSize: 14,
};

const primaryBtn: React.CSSProperties = {
  padding: "6px 12px",
  border: "1px solid #4f46e5",
  background: "#4f46e5",
  color: "white",
  borderRadius: 4,
  fontSize: 13,
  cursor: "pointer",
};

const secondaryBtn: React.CSSProperties = {
  padding: "6px 12px",
  border: "1px solid #d1d5db",
  background: "white",
  color: "#111",
  borderRadius: 4,
  fontSize: 13,
  cursor: "pointer",
};

const dangerBtn: React.CSSProperties = {
  padding: "5px 10px",
  border: "1px solid #fca5a5",
  background: "white",
  color: "#b91c1c",
  borderRadius: 4,
  fontSize: 12,
  cursor: "pointer",
};

const errorBanner: React.CSSProperties = {
  padding: 10,
  background: "#fef2f2",
  color: "#991b1b",
  borderRadius: 6,
  fontSize: 13,
  marginBottom: 8,
};
