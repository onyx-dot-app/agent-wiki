# Phase 2 — Frontend Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship the wiki-side UI for launching coding tools — a wizard that (a) detects MCP-token / helper / CLI setup state, (b) lets the user pick a tool + working directory + message, (c) posts `/api/launch` and navigates to the `agentwiki://` URI, (d) surfaces active sessions live on every wiki page. Backend Phase 1 already merged. No npm helper yet — wizard shows "install launcher" pane permanently when probe fails.

**Architecture:** Next.js 14 App Router + TypeScript. All network through `apiFetch` from `src/lib/api.ts`. All auth state through `useAuth`/`useRequireAuth`. All colors/radii/shadows through `src/lib/theme.ts`. All buttons through `<Button>`. New library `src/lib/launchers.ts` for the typed API client + helper-probe utility + SWR hooks. Wizard composes from small, single-responsibility components. Verify in light + dark + mobile per CLAUDE.md before declaring done.

**Tech Stack:** Next.js 14, TypeScript, SWR, inline-styles via `theme.ts`. Tests via Vitest + React Testing Library where they add value (component behavior, not snapshot churn). End-to-end via Playwright against `localhost:3088` if available.

**Reference:** [../design.md](../design.md) sections "Wizard UX" + "New frontend modules". Resolved P1 items already baked into the API contract from Phase 1.

---

## Pre-flight

- [ ] **Step 0.1: Confirm Phase 1 merged + flag set**

```bash
cd /Users/nikolas/agent-wiki
git -C . log --oneline -5
```

Phase 1 commits should be present. `LAUNCHERS_ENABLED=true` in `.env` for local dev. Backend running on `http://127.0.0.1:8088`.

- [ ] **Step 0.2: Confirm frontend deps**

```bash
cd /Users/nikolas/agent-wiki/frontend
npm install
```

- [ ] **Step 0.3: Start dev server**

```bash
cd /Users/nikolas/agent-wiki/frontend
BACKEND_URL=http://127.0.0.1:8088 npx next dev -p 3088
```

Verify the wiki loads at `http://localhost:3088` with no console errors.

---

## File Structure

```
frontend/src/
  lib/
    launchers.ts                              (create — API client + probe + SWR hooks)
  components/
    wiki/
      RunAgentModal.tsx                       (rewrite — wizard host)
      ActiveSessionsList.tsx                  (create — file-viewer widget)
    agents/
      SetupWizard.tsx                         (create — multi-step)
      ToolCard.tsx                            (create — catalog row)
      ToolStatusBadge.tsx                     (create — ✓/⚠ pill)
      InstallHelperPane.tsx                   (create — copy-paste npm install)
      WorkingDirInput.tsx                     (create — autocomplete + remember checkbox)
  app/
    agents/page.tsx                           (modify — add Coding-tools section)
    wiki/[[...slug]]/page.tsx                 (modify — wire ActiveSessionsList; pass current path to modal)
```

---

## Task 1: Typed launcher API client

**Files:**

- Create: `frontend/src/lib/launchers.ts`

- [ ] **Step 1.1: Write the module**

```typescript
/** Typed wrappers for the launchers + agent-sessions API surface. */
import useSWR from "swr";

import { apiFetch } from "@/lib/api";

export type LauncherKind = "local_cli" | "in_app" | "web_handoff";

export interface LauncherSetupStatus {
  token: boolean;
}

export interface LauncherCatalogEntry {
  id: string;
  name: string;
  tagline: string;
  icon_url: string;
  kind: LauncherKind;
  setup_status: LauncherSetupStatus;
}

export interface LauncherCatalog {
  launchers: LauncherCatalogEntry[];
}

export interface LaunchRequest {
  tool_id: string;
  wiki_path: string | null;
  working_dir: string | null;
  message: string;
  resume_session_id?: string;
  remember_workdir_for_page?: boolean;
}

export interface LaunchResponse {
  launch_code: string;
  uri: string;
  agent_session_id: string;
}

export interface AgentSessionSummary {
  id: string;
  tool_id: string;
  wiki_path: string | null;
  working_dir: string | null;
  status: "pending" | "active" | "idle" | "closed" | "failed";
  started_at: string;
  last_activity_at: string;
  closed_at: string | null;
  cli_session_id: string | null;
}

export interface AgentSessionList {
  sessions: AgentSessionSummary[];
}

export function useLauncherCatalog() {
  const { data, error, isLoading, mutate } =
    useSWR<LauncherCatalog>("/launchers");
  return {
    launchers: data?.launchers ?? [],
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}

export function useAgentSessions(wikiPath?: string) {
  const key = wikiPath
    ? `/agent-sessions?wiki_path=${encodeURIComponent(wikiPath)}`
    : "/agent-sessions";
  const { data, error, isLoading, mutate } = useSWR<AgentSessionList>(key, {
    refreshInterval: 5000,
  });
  return {
    sessions: data?.sessions ?? [],
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}

export function launch(req: LaunchRequest): Promise<LaunchResponse> {
  return apiFetch<LaunchResponse>("/launch", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export function closeSession(id: string, reason: string): Promise<void> {
  return apiFetch<void>(`/agent-sessions/${id}/close`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

/** Helper-presence probe. Creates a hidden iframe pointing at the
 * `agentwiki://` scheme; the helper's URI handler POSTs back to
 * `/api/launch/probe-ack`. We poll `/api/launch/probe-status` for up
 * to ~800ms. Cached per-browser-session in `sessionStorage`. */
export async function probeHelper(): Promise<{
  acked: boolean;
  helperPort: number | null;
}> {
  const cached =
    typeof window !== "undefined"
      ? sessionStorage.getItem("agentwiki:helper-probe")
      : null;
  if (cached) return JSON.parse(cached);

  const nonce = `n_${Math.random().toString(36).slice(2)}_${Date.now()}`;
  const iframe = document.createElement("iframe");
  iframe.style.display = "none";
  iframe.src = `agentwiki://probe?nonce=${encodeURIComponent(nonce)}`;
  document.body.appendChild(iframe);

  const startedAt = Date.now();
  const timeoutMs = 800;
  while (Date.now() - startedAt < timeoutMs) {
    await new Promise((r) => setTimeout(r, 100));
    try {
      const status = await apiFetch<{
        acked: boolean;
        helper_port: number | null;
      }>(`/launch/probe-status?nonce=${encodeURIComponent(nonce)}`);
      if (status.acked) {
        document.body.removeChild(iframe);
        const result = { acked: true, helperPort: status.helper_port };
        sessionStorage.setItem(
          "agentwiki:helper-probe",
          JSON.stringify(result),
        );
        return result;
      }
    } catch {
      // probe-status is allowed to 404 if flag is off; treat as not acked
    }
  }

  document.body.removeChild(iframe);
  const result = { acked: false, helperPort: null };
  sessionStorage.setItem("agentwiki:helper-probe", JSON.stringify(result));
  return result;
}

/** CLI presence probe — talks to the helper's localhost port. Helper
 * returns `{ [tool_id]: { present, version, meets_min } }`. */
export async function probeCli(
  port: number,
  toolIds: string[],
): Promise<
  Record<
    string,
    { present: boolean; version: string | null; meets_min: boolean }
  >
> {
  const res = await fetch(`http://127.0.0.1:${port}/probe-cli`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tool_ids: toolIds }),
  });
  return res.json();
}
```

- [ ] **Step 1.2: Verify it compiles**

```bash
cd /Users/nikolas/agent-wiki/frontend
npm run typecheck
```

Expected: PASS.

- [ ] **Step 1.3: Commit**

```bash
git -C /Users/nikolas/agent-wiki add frontend/src/lib/launchers.ts
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): typed frontend API client + helper/CLI probes"
```

---

## Task 2: `ToolStatusBadge` component

**Files:**

- Create: `frontend/src/components/agents/ToolStatusBadge.tsx`

Small pill with ✓ / ⚠ / · semantics. Used by `ToolCard` and `SetupWizard`.

- [ ] **Step 2.1: Write**

```tsx
"use client";

import { color, radius } from "@/lib/theme";

type Status = "ok" | "warn" | "muted";

export function ToolStatusBadge({
  status,
  label,
}: {
  status: Status;
  label: string;
}) {
  const palette =
    status === "ok"
      ? {
          bg: color.state.success.bg,
          fg: color.state.success.fg,
          border: color.state.success.border,
          glyph: "✓",
        }
      : status === "warn"
        ? {
            bg: color.state.warning.bg,
            fg: color.state.warning.fg,
            border: color.state.warning.border,
            glyph: "⚠",
          }
        : {
            bg: color.bg.sunken,
            fg: color.text.muted,
            border: color.border.subtle,
            glyph: "·",
          };

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 8px",
        fontSize: 12,
        fontWeight: 500,
        color: palette.fg,
        background: palette.bg,
        border: `1px solid ${palette.border}`,
        borderRadius: radius.pill,
      }}
    >
      <span aria-hidden="true">{palette.glyph}</span>
      {label}
    </span>
  );
}
```

- [ ] **Step 2.2: Typecheck + commit**

```bash
cd /Users/nikolas/agent-wiki/frontend && npm run typecheck
git -C /Users/nikolas/agent-wiki add frontend/src/components/agents/ToolStatusBadge.tsx
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): ToolStatusBadge"
```

---

## Task 3: `InstallHelperPane` component

**Files:**

- Create: `frontend/src/components/agents/InstallHelperPane.tsx`

Renders the `npm install -g @agentwiki/launcher` command + Copy button + "I've installed it" CTA that re-runs the probe.

- [ ] **Step 3.1: Write**

```tsx
"use client";

import { useState } from "react";

import { Button } from "@/components/common/Button";
import { color, radius } from "@/lib/theme";

const INSTALL_CMD = "npm install -g @agentwiki/launcher";

export function InstallHelperPane({
  onReprobe,
}: {
  onReprobe: () => Promise<void> | void;
}) {
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(INSTALL_CMD);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  }

  async function reprobe() {
    setBusy(true);
    try {
      sessionStorage.removeItem("agentwiki:helper-probe");
      await onReprobe();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      style={{
        padding: 12,
        background: color.state.warning.bg,
        border: `1px solid ${color.state.warning.border}`,
        borderRadius: radius.sm,
      }}
    >
      <div
        style={{ fontSize: 13, color: color.state.warning.fg, marginBottom: 8 }}
      >
        Launcher isn&apos;t installed on this machine. Run:
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <code
          style={{
            flex: 1,
            padding: "6px 8px",
            background: color.bg.sunken,
            borderRadius: radius.xs,
            fontFamily: "ui-monospace, Menlo, monospace",
            fontSize: 12,
            color: color.text.primary,
          }}
        >
          {INSTALL_CMD}
        </code>
        <Button size="sm" onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <div
        style={{ marginTop: 10, display: "flex", justifyContent: "flex-end" }}
      >
        <Button size="sm" variant="primary" onClick={reprobe} disabled={busy}>
          {busy ? "Checking..." : "I've installed it"}
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3.2: Commit**

```bash
cd /Users/nikolas/agent-wiki/frontend && npm run typecheck
git -C /Users/nikolas/agent-wiki add frontend/src/components/agents/InstallHelperPane.tsx
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): InstallHelperPane"
```

---

## Task 4: `ToolCard` component

**Files:**

- Create: `frontend/src/components/agents/ToolCard.tsx`

Catalog row: icon, name, tagline, status badges, optional Select radio.

- [ ] **Step 4.1: Write**

```tsx
"use client";

import { color, radius } from "@/lib/theme";

import { ToolStatusBadge } from "./ToolStatusBadge";

interface Props {
  id: string;
  name: string;
  tagline: string;
  iconUrl: string;
  selected: boolean;
  onSelect?: () => void;
  tokenReady: boolean;
  helperReady: boolean;
  cliReady: boolean | null; // null = unknown (helper not detected → can't probe)
}

export function ToolCard({
  id,
  name,
  tagline,
  iconUrl,
  selected,
  onSelect,
  tokenReady,
  helperReady,
  cliReady,
}: Props) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
        textAlign: "left",
        width: "100%",
        padding: 12,
        background: selected ? color.accent.subtleBg : color.bg.page,
        border: `1px solid ${
          selected ? color.accent.bg : color.border.default
        }`,
        borderRadius: radius.md,
        cursor: onSelect ? "pointer" : "default",
        gap: 8,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          width: "100%",
        }}
      >
        <img src={iconUrl} alt="" width={24} height={24} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{ fontSize: 14, fontWeight: 600, color: color.text.primary }}
          >
            {name}
          </div>
          <div style={{ fontSize: 12, color: color.text.muted, marginTop: 1 }}>
            {tagline}
          </div>
        </div>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <ToolStatusBadge
          status={tokenReady ? "ok" : "warn"}
          label={tokenReady ? "Token" : "Need token"}
        />
        <ToolStatusBadge
          status={helperReady ? "ok" : "warn"}
          label={helperReady ? "Launcher" : "No launcher"}
        />
        <ToolStatusBadge
          status={cliReady === null ? "muted" : cliReady ? "ok" : "warn"}
          label={
            cliReady === null ? "CLI: ?" : cliReady ? "CLI" : `${id} missing`
          }
        />
      </div>
    </button>
  );
}
```

- [ ] **Step 4.2: Commit**

```bash
cd /Users/nikolas/agent-wiki/frontend && npm run typecheck
git -C /Users/nikolas/agent-wiki add frontend/src/components/agents/ToolCard.tsx
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): ToolCard"
```

---

## Task 5: `WorkingDirInput` component

**Files:**

- Create: `frontend/src/components/agents/WorkingDirInput.tsx`

Input with optional remember-for-page checkbox. v1 keeps it simple (text input + checkbox); autocomplete from recent dirs is a v2 follow-up.

- [ ] **Step 5.1: Write**

```tsx
"use client";

import { color, radius } from "@/lib/theme";

interface Props {
  value: string;
  onChange: (v: string) => void;
  remember: boolean;
  onRememberChange: (v: boolean) => void;
  pageHasBinding: boolean;
}

export function WorkingDirInput({
  value,
  onChange,
  remember,
  onRememberChange,
  pageHasBinding,
}: Props) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <label
        style={{ fontSize: 12, color: color.text.secondary, fontWeight: 600 }}
      >
        Working directory
      </label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="(leave blank for scratch directory)"
        style={{
          padding: "8px 10px",
          border: `1px solid ${color.border.default}`,
          borderRadius: radius.sm,
          fontSize: 14,
          fontFamily: "ui-monospace, Menlo, monospace",
        }}
      />
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: 12,
          color: color.text.muted,
        }}
      >
        <input
          type="checkbox"
          checked={remember}
          onChange={(e) => onRememberChange(e.target.checked)}
        />
        {pageHasBinding
          ? "Update default for this page"
          : "Remember as default for this page"}
      </label>
    </div>
  );
}
```

- [ ] **Step 5.2: Commit**

```bash
cd /Users/nikolas/agent-wiki/frontend && npm run typecheck
git -C /Users/nikolas/agent-wiki add frontend/src/components/agents/WorkingDirInput.tsx
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): WorkingDirInput"
```

---

## Task 6: `SetupWizard` component

**Files:**

- Create: `frontend/src/components/agents/SetupWizard.tsx`

Multi-step wizard:

- Step 1: pick tools (multi-select from catalog)
- Step 2: per-tool checklist (token ✓, helper ✓, CLI ✓)

- [ ] **Step 6.1: Write**

```tsx
"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/common/Button";
import { color, radius } from "@/lib/theme";
import { LauncherCatalogEntry, probeCli, probeHelper } from "@/lib/launchers";

import { InstallHelperPane } from "./InstallHelperPane";
import { ToolStatusBadge } from "./ToolStatusBadge";

interface Props {
  catalog: LauncherCatalogEntry[];
  onDone: () => void;
  onCancel: () => void;
}

interface CliStatus {
  present: boolean;
  version: string | null;
  meets_min: boolean;
}

export function SetupWizard({ catalog, onDone, onCancel }: Props) {
  const [step, setStep] = useState<1 | 2>(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [helperState, setHelperState] = useState<{
    acked: boolean;
    port: number | null;
  } | null>(null);
  const [cliState, setCliState] = useState<Record<string, CliStatus> | null>(
    null,
  );
  const [probing, setProbing] = useState(false);

  async function runProbes() {
    setProbing(true);
    try {
      const h = await probeHelper();
      setHelperState({ acked: h.acked, port: h.helperPort });
      if (h.acked && h.helperPort && selected.size > 0) {
        const ids = Array.from(selected).filter(
          (id) => catalog.find((c) => c.id === id)?.kind === "local_cli",
        );
        if (ids.length > 0) {
          const c = await probeCli(h.helperPort, ids);
          setCliState(c);
        }
      } else {
        setCliState({});
      }
    } finally {
      setProbing(false);
    }
  }

  useEffect(() => {
    if (step === 2) void runProbes();
  }, [step]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {step === 1 && (
        <Step1
          catalog={catalog}
          selected={selected}
          onToggle={(id) => {
            const next = new Set(selected);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            setSelected(next);
          }}
          onCancel={onCancel}
          onNext={() => setStep(2)}
        />
      )}
      {step === 2 && (
        <Step2
          catalog={catalog.filter((c) => selected.has(c.id))}
          helperState={helperState}
          cliState={cliState}
          probing={probing}
          onReprobe={runProbes}
          onBack={() => setStep(1)}
          onDone={onDone}
        />
      )}
    </div>
  );
}

function Step1({
  catalog,
  selected,
  onToggle,
  onCancel,
  onNext,
}: {
  catalog: LauncherCatalogEntry[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  onCancel: () => void;
  onNext: () => void;
}) {
  return (
    <>
      <div style={{ fontSize: 14, color: color.text.primary, fontWeight: 600 }}>
        Pick which tools to set up — step 1 of 2
      </div>
      <div style={{ fontSize: 12, color: color.text.muted }}>
        You can add more later from the Agents page.
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
        {catalog.map((c) => (
          <li key={c.id}>
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: 10,
                border: `1px solid ${color.border.default}`,
                borderRadius: radius.sm,
                cursor: "pointer",
              }}
            >
              <input
                type="checkbox"
                checked={selected.has(c.id)}
                onChange={() => onToggle(c.id)}
              />
              <img src={c.icon_url} alt="" width={20} height={20} />
              <div style={{ flex: 1 }}>
                <div
                  style={{
                    fontSize: 14,
                    fontWeight: 600,
                    color: color.text.primary,
                  }}
                >
                  {c.name}
                </div>
                <div style={{ fontSize: 12, color: color.text.muted }}>
                  {c.tagline}
                </div>
              </div>
              <ToolStatusBadge
                status="muted"
                label={c.kind === "in_app" ? "in-app" : "terminal"}
              />
            </label>
          </li>
        ))}
      </ul>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <Button onClick={onCancel}>Cancel</Button>
        <Button
          variant="primary"
          onClick={onNext}
          disabled={selected.size === 0}
        >
          Next
        </Button>
      </div>
    </>
  );
}

function Step2({
  catalog,
  helperState,
  cliState,
  probing,
  onReprobe,
  onBack,
  onDone,
}: {
  catalog: LauncherCatalogEntry[];
  helperState: { acked: boolean; port: number | null } | null;
  cliState: Record<string, CliStatus> | null;
  probing: boolean;
  onReprobe: () => Promise<void>;
  onBack: () => void;
  onDone: () => void;
}) {
  const allOk =
    !probing &&
    helperState?.acked &&
    catalog
      .filter((c) => c.kind === "local_cli")
      .every((c) => cliState?.[c.id]?.meets_min);

  return (
    <>
      <div style={{ fontSize: 14, color: color.text.primary, fontWeight: 600 }}>
        Setup checklist — step 2 of 2
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {catalog.map((c) => (
          <div
            key={c.id}
            style={{
              padding: 12,
              border: `1px solid ${color.border.default}`,
              borderRadius: radius.sm,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                marginBottom: 6,
              }}
            >
              <img src={c.icon_url} alt="" width={20} height={20} />
              <strong style={{ fontSize: 14 }}>{c.name}</strong>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <ToolStatusBadge
                status={c.setup_status.token ? "ok" : "warn"}
                label={
                  c.setup_status.token
                    ? "Token ready"
                    : "Token will auto-mint on launch"
                }
              />
              {c.kind === "local_cli" && (
                <>
                  <ToolStatusBadge
                    status={helperState?.acked ? "ok" : "warn"}
                    label={
                      helperState?.acked
                        ? "Launcher detected"
                        : "Launcher not installed"
                    }
                  />
                  {helperState?.acked && cliState && (
                    <ToolStatusBadge
                      status={cliState[c.id]?.meets_min ? "ok" : "warn"}
                      label={
                        cliState[c.id]?.meets_min
                          ? `CLI ${cliState[c.id]?.version} ready`
                          : `${c.id} not in PATH`
                      }
                    />
                  )}
                </>
              )}
            </div>
          </div>
        ))}
        {!helperState?.acked && <InstallHelperPane onReprobe={onReprobe} />}
      </div>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <Button onClick={onBack}>Back</Button>
        <Button variant="primary" onClick={onDone} disabled={!allOk}>
          Done
        </Button>
      </div>
    </>
  );
}
```

- [ ] **Step 6.2: Commit**

```bash
cd /Users/nikolas/agent-wiki/frontend && npm run typecheck
git -C /Users/nikolas/agent-wiki add frontend/src/components/agents/SetupWizard.tsx
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): SetupWizard component"
```

---

## Task 7: `RunAgentModal` rewrite

**Files:**

- Modify: `frontend/src/components/wiki/RunAgentModal.tsx`

Rewrite from stub into wizard host. States:

- **State A** (steady state): tool radio, working dir, message textarea, active sessions, Run button.
- **State B** (setup wizard): rendered when no tool is fully set up, or user clicks "Set up another tool".

After launch, navigate to the returned `agentwiki://` URI.

- [ ] **Step 7.1: Rewrite**

```tsx
"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/common/Button";
import { SetupWizard } from "@/components/agents/SetupWizard";
import { ToolCard } from "@/components/agents/ToolCard";
import { WorkingDirInput } from "@/components/agents/WorkingDirInput";
import { ApiError } from "@/lib/api";
import {
  launch,
  probeHelper,
  useAgentSessions,
  useLauncherCatalog,
  type LauncherCatalogEntry,
} from "@/lib/launchers";
import { color, radius, shadow } from "@/lib/theme";

interface Props {
  open: boolean;
  onClose: () => void;
  wikiPath: string | null;
}

export function RunAgentModal({ open, onClose, wikiPath }: Props) {
  const { launchers } = useLauncherCatalog();
  const { sessions, refresh: refreshSessions } = useAgentSessions(
    wikiPath ?? undefined,
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [workingDir, setWorkingDir] = useState("");
  const [rememberWorkdir, setRememberWorkdir] = useState(false);
  const [message, setMessage] = useState("");
  const [helperAcked, setHelperAcked] = useState<boolean | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setMessage("");
    setError(null);
    setSelectedId(launchers[0]?.id ?? null);
    void probeHelper().then((r) => setHelperAcked(r.acked));
  }, [open, launchers]);

  useEffect(() => {
    if (!open) return;
    const fn = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, [open, onClose]);

  if (!open) return null;

  async function onRun(e: FormEvent) {
    e.preventDefault();
    if (!selectedId) return;
    if (helperAcked === false) {
      setWizardOpen(true);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await launch({
        tool_id: selectedId,
        wiki_path: wikiPath,
        working_dir: workingDir.trim() || null,
        message,
        remember_workdir_for_page: rememberWorkdir,
      });
      window.location.href = res.uri;
      onClose();
      await refreshSessions();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to launch");
      setBusy(false);
    }
  }

  const canRun = message.trim().length > 0 && selectedId !== null;

  return (
    <div
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
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
      <form
        onSubmit={onRun}
        role="dialog"
        aria-modal="true"
        aria-label="Run agent"
        style={{
          background: color.bg.page,
          borderRadius: radius.lg,
          width: "min(560px, 92vw)",
          padding: 22,
          boxShadow: shadow.modal,
          display: "flex",
          flexDirection: "column",
          gap: 14,
          maxHeight: "90vh",
          overflowY: "auto",
        }}
      >
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Run agent</h2>

        {wizardOpen ? (
          <SetupWizard
            catalog={launchers}
            onDone={() => setWizardOpen(false)}
            onCancel={() => setWizardOpen(false)}
          />
        ) : (
          <>
            <ToolList
              catalog={launchers}
              selectedId={selectedId}
              onSelect={setSelectedId}
              helperAcked={helperAcked}
            />

            <WorkingDirInput
              value={workingDir}
              onChange={setWorkingDir}
              remember={rememberWorkdir}
              onRememberChange={setRememberWorkdir}
              pageHasBinding={false}
            />

            <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span
                style={{
                  fontSize: 12,
                  color: color.text.secondary,
                  fontWeight: 600,
                }}
              >
                Message
              </span>
              <textarea
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="What should the agent do with this doc?"
                rows={4}
                style={{
                  padding: 10,
                  border: `1px solid ${color.border.default}`,
                  borderRadius: radius.md,
                  fontFamily: "inherit",
                  fontSize: 14,
                  lineHeight: 1.5,
                  resize: "vertical",
                  minHeight: 96,
                  color: color.text.primary,
                  background: color.bg.page,
                }}
              />
            </label>

            {sessions.length > 0 && (
              <div>
                <div
                  style={{
                    fontSize: 12,
                    color: color.text.secondary,
                    fontWeight: 600,
                    marginBottom: 4,
                  }}
                >
                  Active sessions on this page
                </div>
                <ul
                  style={{
                    listStyle: "none",
                    padding: 0,
                    margin: 0,
                    fontSize: 13,
                  }}
                >
                  {sessions.map((s) => (
                    <li key={s.id} style={{ color: color.text.muted }}>
                      {s.tool_id} · {s.status} · {s.started_at}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {error && (
              <div
                style={{
                  padding: 8,
                  background: color.state.danger.bg,
                  color: color.state.danger.fg,
                  borderRadius: radius.sm,
                  fontSize: 13,
                }}
              >
                {error}
              </div>
            )}

            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginTop: 4,
              }}
            >
              <button
                type="button"
                onClick={() => setWizardOpen(true)}
                style={{
                  background: "transparent",
                  border: "none",
                  color: color.text.muted,
                  fontSize: 12,
                  cursor: "pointer",
                  padding: 0,
                }}
              >
                Set up another tool →
              </button>
              <div style={{ display: "flex", gap: 8 }}>
                <Button type="button" onClick={onClose}>
                  Cancel
                </Button>
                <Button
                  type="submit"
                  variant="primary"
                  disabled={!canRun || busy}
                >
                  {busy ? "Launching..." : "Run"}
                </Button>
              </div>
            </div>
          </>
        )}
      </form>
    </div>
  );
}

function ToolList({
  catalog,
  selectedId,
  onSelect,
  helperAcked,
}: {
  catalog: LauncherCatalogEntry[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  helperAcked: boolean | null;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span
        style={{ fontSize: 12, color: color.text.secondary, fontWeight: 600 }}
      >
        Tool
      </span>
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
        {catalog.map((c) => (
          <li key={c.id}>
            <ToolCard
              id={c.id}
              name={c.name}
              tagline={c.tagline}
              iconUrl={c.icon_url}
              selected={c.id === selectedId}
              onSelect={() => onSelect(c.id)}
              tokenReady={c.setup_status.token}
              helperReady={c.kind === "in_app" || helperAcked === true}
              cliReady={c.kind === "in_app" ? true : helperAcked ? null : false}
            />
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 7.2: Update the caller in the wiki file viewer**

Open `frontend/src/app/wiki/[[...slug]]/page.tsx`. Find the `<RunAgentModal ... />` JSX (around line 1144). Pass `wikiPath`:

```tsx
<RunAgentModal
  open={runAgentOpen}
  onClose={() => setRunAgentOpen(false)}
  wikiPath={slugPath || null}
/>
```

- [ ] **Step 7.3: Typecheck**

```bash
cd /Users/nikolas/agent-wiki/frontend && npm run typecheck
```

- [ ] **Step 7.4: Manual smoke (light + dark + mobile)**

1. Start dev server: `BACKEND_URL=http://127.0.0.1:8088 npx next dev -p 3088`
2. Open `http://localhost:3088/wiki/architecture.md` in light mode.
3. Click Run Agent → confirm modal opens, tool list renders, message textarea works.
4. Toggle to dark mode (theme picker) → verify modal colors stay legible.
5. Resize browser to 375px wide → verify modal fits, buttons reachable.
6. Hit Cancel → modal closes.

- [ ] **Step 7.5: Commit**

```bash
git -C /Users/nikolas/agent-wiki add frontend/src/components/wiki/RunAgentModal.tsx frontend/src/app/wiki/[[...slug]]/page.tsx
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): RunAgentModal wizard host"
```

---

## Task 8: `ActiveSessionsList` widget

**Files:**

- Create: `frontend/src/components/wiki/ActiveSessionsList.tsx`
- Modify: `frontend/src/app/wiki/[[...slug]]/page.tsx` (inject into file viewer header)

- [ ] **Step 8.1: Write**

```tsx
"use client";

import { Button } from "@/components/common/Button";
import { closeSession, useAgentSessions } from "@/lib/launchers";
import { color, radius } from "@/lib/theme";

export function ActiveSessionsList({ wikiPath }: { wikiPath: string }) {
  const { sessions, refresh } = useAgentSessions(wikiPath);
  const active = sessions.filter(
    (s) => s.status === "active" || s.status === "idle",
  );
  if (active.length === 0) return null;

  async function onClose(id: string) {
    await closeSession(id, "user_clicked");
    await refresh();
  }

  return (
    <div
      style={{
        padding: 8,
        background: color.accent.subtleBg,
        border: `1px solid ${color.accent.subtleBorder}`,
        borderRadius: radius.sm,
        fontSize: 12,
      }}
    >
      <div
        style={{
          fontWeight: 600,
          color: color.accent.subtleFg,
          marginBottom: 4,
        }}
      >
        {active.length} agent session{active.length > 1 ? "s" : ""} on this page
      </div>
      <ul
        style={{
          listStyle: "none",
          padding: 0,
          margin: 0,
          display: "flex",
          flexDirection: "column",
          gap: 4,
        }}
      >
        {active.map((s) => (
          <li
            key={s.id}
            style={{ display: "flex", alignItems: "center", gap: 8 }}
          >
            <span style={{ flex: 1, color: color.text.primary }}>
              {s.tool_id} · {s.status} · started {s.started_at}
            </span>
            <Button size="sm" variant="ghost" onClick={() => onClose(s.id)}>
              Close
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 8.2: Inject into file viewer**

Open `frontend/src/app/wiki/[[...slug]]/page.tsx`. Find the file-viewer header section (where Run Agent + Trigger + Share buttons live, around lines 1075-1083). Add above or beside the button row:

```tsx
<ActiveSessionsList wikiPath={slugPath} />
```

And add the import:

```tsx
import { ActiveSessionsList } from "@/components/wiki/ActiveSessionsList";
```

- [ ] **Step 8.3: Typecheck + smoke**

```bash
cd /Users/nikolas/agent-wiki/frontend && npm run typecheck
```

Verify the widget renders empty when no sessions exist (most pages), and lights up after a Run Agent click.

- [ ] **Step 8.4: Commit**

```bash
git -C /Users/nikolas/agent-wiki add frontend/src/components/wiki/ActiveSessionsList.tsx frontend/src/app/wiki/[[...slug]]/page.tsx
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): ActiveSessionsList widget on file viewer"
```

---

## Task 9: `/agents` page — Coding-tools section

**Files:**

- Modify: `frontend/src/app/agents/page.tsx`

Add a section above the existing "API keys" block. Renders the catalog with status badges + a "Set up another tool" button that opens `SetupWizard` in a modal.

- [ ] **Step 9.1: Add section**

In `frontend/src/app/agents/page.tsx`, in the `AgentsPage` function, before `<TokenManager />`, add:

```tsx
<CodingToolsSection />
```

And below `ClientConfigHelp`, add:

```tsx
function CodingToolsSection() {
  const { launchers } = useLauncherCatalog();
  const [wizardOpen, setWizardOpen] = useState(false);
  const [helperAcked, setHelperAcked] = useState<boolean | null>(null);

  useEffect(() => {
    void probeHelper().then((r) => setHelperAcked(r.acked));
  }, []);

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
              onSelect={undefined}
              tokenReady={c.setup_status.token}
              helperReady={c.kind === "in_app" || helperAcked === true}
              cliReady={c.kind === "in_app" ? true : helperAcked ? null : false}
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
```

Add the imports at the top of the file:

```tsx
import { SetupWizard } from "@/components/agents/SetupWizard";
import { ToolCard } from "@/components/agents/ToolCard";
import { probeHelper, useLauncherCatalog } from "@/lib/launchers";
import { shadow } from "@/lib/theme";
```

- [ ] **Step 9.2: Typecheck + smoke**

Visit `/agents` in browser. Verify Coding-tools section renders. Light + dark + mobile pass.

- [ ] **Step 9.3: Commit**

```bash
git -C /Users/nikolas/agent-wiki add frontend/src/app/agents/page.tsx
git -C /Users/nikolas/agent-wiki commit -m "feat(launchers): /agents page Coding tools section"
```

---

## Task 10: End-to-end smoke against running backend

- [ ] **Step 10.1: Start backend + workers + frontend in separate terminals**

```bash
# Terminal A
cd /Users/nikolas/agent-wiki/backend && uv run --extra dev uvicorn --factory app.main:create_app --host 127.0.0.1 --port 8088

# Terminal B
cd /Users/nikolas/agent-wiki/backend && uv run --extra dev python -m app.tasks.run_worker lightweight_maintenance

# Terminal C
cd /Users/nikolas/agent-wiki/frontend && BACKEND_URL=http://127.0.0.1:8088 npx next dev -p 3088
```

- [ ] **Step 10.2: Login, open a page, click Run Agent**

In a browser:

1. Sign in at `http://localhost:3088`.
2. Navigate to any `.md` page.
3. Click Run Agent.
4. Confirm catalog loads with 3 tools.
5. Confirm helper probe shows "Launcher not installed" (because Phase 3's npm package isn't built yet) — this is expected.
6. Confirm the modal lets you pick a tool, set a working dir, type a message, hit Run.
7. After Run: browser tries to navigate to `agentwiki://...` URI. Modern browsers will show an "open with" dialog or 404 because no handler is installed. This is expected — Phase 3 ships the handler.
8. Confirm a row appears in `/agents` Coding tools section.

- [ ] **Step 10.3: Inspect DB**

```bash
psql agent_wiki -c "SELECT id, status, tool_id FROM agent_sessions ORDER BY started_at DESC LIMIT 5;"
psql agent_wiki -c "SELECT id, expires_at, consumed_at FROM launch_codes ORDER BY created_at DESC LIMIT 5;"
```

Expected: one row in `agent_sessions` per Run-Agent click. `launch_codes` rows expire and get swept by `expire_launch_artifacts` after 60s + sweep tick.

- [ ] **Step 10.4: Light + dark + mobile audit**

Walk through every modified surface in both themes and at 375px viewport:

- `RunAgentModal` (open + with sessions list populated + setup wizard mode)
- `ActiveSessionsList`
- `/agents` Coding tools section
- Step 1 + Step 2 of `SetupWizard`

No raw hex colors, no overflow at 375px, no unreadable text on either theme.

- [ ] **Step 10.5: Pre-commit + push**

```bash
cd /Users/nikolas/agent-wiki && pre-commit run --files $(git diff --name-only main...HEAD)
git -C /Users/nikolas/agent-wiki push
```

---

## Done

After Task 10, Phase 2 frontend is shippable:

- Wizard renders; tool catalog populated; helper probe runs.
- Run Agent flows through `/api/launch` and navigates to the `agentwiki://` URI.
- Active sessions live-poll every 5s and render on each file page.
- `/agents` page has a top-level Coding tools section.
- All UI works in light + dark + mobile.
- No helper yet → setup wizard's launcher status is permanent ⚠ until Phase 3 ships the npm package.
