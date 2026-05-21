/** Typed wrappers for the launchers + agent-sessions API surface.
 *
 * See `local_data/wiki/Wiki Project/Specific Features/coding_tool_launchers/`.
 */
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
  kind: LauncherKind;
  available_for_launch: boolean;
  setup_status: LauncherSetupStatus;
  default_working_dir: string | null;
  unscoped_workdir_warning: string | null;
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
  machine_id?: string;
  remember_workdir_for_page?: boolean;
}

export interface LaunchResponse {
  launch_code: string;
  uri: string;
  agent_session_id: string;
}

export type AgentSessionStatus =
  | "pending"
  | "active"
  | "idle"
  | "closed"
  | "failed";

export interface AgentSessionSummary {
  id: string;
  tool_id: string;
  wiki_path: string | null;
  working_dir: string | null;
  status: AgentSessionStatus;
  started_at: string;
  last_activity_at: string;
  closed_at: string | null;
  cli_session_id: string | null;
}

export interface AgentSessionList {
  sessions: AgentSessionSummary[];
}

export interface ProbeResult {
  acked: boolean;
  helperPort: number | null;
  machineId: string | null;
}

// --------------------------------------------------------------------------- //
// SWR hooks                                                                   //
// --------------------------------------------------------------------------- //

export function useLauncherCatalog(
  opts: {
    machineId?: string | null;
    wikiPath?: string | null;
  } = {},
) {
  const params = new URLSearchParams();
  if (opts.machineId) params.set("machine_id", opts.machineId);
  if (opts.wikiPath) params.set("wiki_path", opts.wikiPath);
  const qs = params.toString();
  const key = qs ? `/launchers?${qs}` : "/launchers";
  const { data, error, isLoading, mutate } = useSWR<LauncherCatalog>(key);
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

// --------------------------------------------------------------------------- //
// Mutations                                                                   //
// --------------------------------------------------------------------------- //

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

// --------------------------------------------------------------------------- //
// Helper probe                                                                //
// --------------------------------------------------------------------------- //

const PROBE_CACHE_KEY = "agentwiki:helper-probe";
// Persistent across tabs/sessions. Set after a successful iframe probe
// so subsequent visits can re-probe to verify; cleared when the user
// hits an install/uninstall flow.
const EVER_INSTALLED_KEY = "agentwiki:ever-installed";

/** Probe with retry + iframe-dispatch ONLY if we've previously seen an
 * ack (i.e. user has installed before). On a first-ever visit with no
 * record of install, return negative immediately without firing the
 * iframe — macOS pops a "no app for URL" dialog for unhandled schemes,
 * spamming the user before they've had a chance to install. The
 * "I've installed it" CTA passes ``opts.force`` to force a real probe
 * after the user has actually installed the helper. */
export async function probeHelper(
  opts: { retries?: number; force?: boolean } = {},
): Promise<ProbeResult> {
  if (typeof window === "undefined") {
    return { acked: false, helperPort: null, machineId: null };
  }
  const cached = sessionStorage.getItem(PROBE_CACHE_KEY);
  if (cached) {
    try {
      return JSON.parse(cached) as ProbeResult;
    } catch {
      sessionStorage.removeItem(PROBE_CACHE_KEY);
    }
  }
  const everInstalled = localStorage.getItem(EVER_INSTALLED_KEY) === "1";
  if (!opts.force && !everInstalled) {
    const negative = { acked: false, helperPort: null, machineId: null };
    sessionStorage.setItem(PROBE_CACHE_KEY, JSON.stringify(negative));
    return negative;
  }
  const retries = opts.retries ?? 3;
  for (let i = 0; i < retries; i++) {
    const result = await probeHelperOnce();
    if (result.acked) {
      sessionStorage.setItem(PROBE_CACHE_KEY, JSON.stringify(result));
      localStorage.setItem(EVER_INSTALLED_KEY, "1");
      return result;
    }
  }
  const negative = { acked: false, helperPort: null, machineId: null };
  sessionStorage.setItem(PROBE_CACHE_KEY, JSON.stringify(negative));
  return negative;
}

async function probeHelperOnce(): Promise<ProbeResult> {
  const nonce = `n_${Math.random().toString(36).slice(2)}_${Date.now()}`;
  const iframe = document.createElement("iframe");
  iframe.style.display = "none";
  try {
    iframe.src = `agentwiki://probe?nonce=${encodeURIComponent(
      nonce,
    )}&endpoint=${encodeURIComponent(window.location.origin)}`;
    document.body.appendChild(iframe);
    const startedAt = Date.now();
    while (Date.now() - startedAt < 800) {
      await sleep(100);
      try {
        const status = await apiFetch<{
          acked: boolean;
          helper_port: number | null;
          machine_id: string | null;
        }>(`/launch/probe-status?nonce=${encodeURIComponent(nonce)}`);
        if (status.acked) {
          return {
            acked: true,
            helperPort: status.helper_port,
            machineId: status.machine_id,
          };
        }
      } catch {
        // probe-status 404s when flag off; treat as not acked.
      }
    }
    return { acked: false, helperPort: null, machineId: null };
  } finally {
    if (iframe.parentNode) iframe.parentNode.removeChild(iframe);
  }
}

/** Invalidate the cached probe result. Call after an explicit
 * "I've installed it" CTA so the next probe runs fresh. */
export function invalidateHelperProbe(): void {
  if (typeof window === "undefined") return;
  sessionStorage.removeItem(PROBE_CACHE_KEY);
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
