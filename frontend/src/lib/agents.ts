/** Typed wrappers for the Agents page (inbound MCP token surface).
 *
 * Endpoints:
 *   GET    /api/mcp/tokens             — list current user's tokens
 *   POST   /api/mcp/tokens   {name}    — mint; returns the raw token once
 *   DELETE /api/mcp/tokens/:id         — revoke
 *
 * The raw token in ``CreatedToken.token`` is the only place the
 * plaintext exists. The page must show it once and warn the user that
 * it can't be recovered.
 */
import useSWR from "swr";

import { apiFetch } from "@/lib/api";

export interface TokenSummary {
  id: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
}

export interface CreatedToken {
  id: string;
  name: string;
  created_at: string;
  /** Plaintext bearer value — shown to the user once at creation. */
  token: string;
}

export function useTokens() {
  const { data, error, isLoading, mutate } = useSWR<{ tokens: TokenSummary[] }>(
    "/mcp/tokens",
  );
  return {
    tokens: data?.tokens ?? [],
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}

export function createToken(name: string): Promise<CreatedToken> {
  return apiFetch<CreatedToken>("/mcp/tokens", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function revokeToken(id: string): Promise<void> {
  return apiFetch<void>(`/mcp/tokens/${id}`, { method: "DELETE" });
}

/** The base URL an MCP client should connect to. Computed from the
 * current window so the page renders the right host (localhost in dev,
 * the deployed origin in prod). Returns ``""`` during SSR — the caller
 * should render a placeholder until the page mounts on the client. */
export function mcpEndpointUrl(): string {
  if (typeof window === "undefined") return "";
  return `${window.location.origin}/api/mcp`;
}
