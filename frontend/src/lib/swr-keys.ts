/**
 * Centralized SWR cache key registry.
 *
 * Every `use*` hook that calls `useSWR` should reference these constants
 * instead of an inline string literal, so a typo can't silently create a
 * second, unlinked cache entry for the same endpoint. Dynamic keys (per-id,
 * per-path, or query-string endpoints) are builder functions.
 */
export const SWR_KEYS = {
  // ── Wiki ──────────────────────────────────────────────────────────────
  wikiTree: "/wiki",
  wikiTrash: "/wiki/trash",
  docIdResolve: (id: string) => `/wiki/id/${id}`,
  deletedTombstone: (path: string) =>
    `/wiki/deleted?path=${encodeURIComponent(path)}`,
  updateHealth: (path: string) =>
    `/wiki/update-health?path=${encodeURIComponent(path)}`,
  documentActivity: (path: string) =>
    `/wiki/file/activity?path=${encodeURIComponent(path)}`,
  /** Tuple key: the head sha busts the cache on new commits even though the
   * request URL only carries the path (the server always reads at HEAD). */
  sourceSpans: (path: string, headSha: string) =>
    ["/wiki/source-spans", path, headSha] as const,
  pageAcl: (path: string) => `/wiki/acl?path=${encodeURIComponent(path)}`,
  /** `usePathToId`'s cache key is a `[tag, path]` tuple — `tag` namespaces two
   * call sites resolving different concerns off the same `resolveIds`
   * endpoint. Keep any tag in sync with the key matcher in
   * `wikiHref.ts:revalidateWiki`. */
  pathToId: (tag: string, path: string) => [tag, path] as const,

  // ── Templates ─────────────────────────────────────────────────────────
  templates: "/templates",
  adminTemplates: "/admin/templates",

  // ── Groups & permissions ──────────────────────────────────────────────
  groups: "/groups",
  group: (id: string) => `/groups/${id}`,
  groupShares: (id: string) => `/groups/${id}/shares`,

  // ── Notifications ─────────────────────────────────────────────────────
  notifications: "/notifications",

  // ── LLM ───────────────────────────────────────────────────────────────
  llmStatus: "/llm/status",

  // ── Auto Organize ─────────────────────────────────────────────────────
  autoOrganizeSettings: "/automanage/settings",
  automanageRuns: "/automanage/runs",
  automanageProposals: (path: string) =>
    `/automanage/proposals?path=${encodeURIComponent(path)}`,

  // ── Agents / MCP ──────────────────────────────────────────────────────
  mcpTokens: "/mcp/tokens",

  // ── Craft ─────────────────────────────────────────────────────────────
  craftConnect: "/craft/connect",

  // ── Launchers / agent sessions ────────────────────────────────────────
  launcherCatalog: (
    opts: { machineId?: string | null; wikiPath?: string | null } = {},
  ) => {
    const params = new URLSearchParams();
    if (opts.machineId) params.set("machine_id", opts.machineId);
    if (opts.wikiPath) params.set("wiki_path", opts.wikiPath);
    const qs = params.toString();
    return qs ? `/launchers?${qs}` : "/launchers";
  },
  agentSessions: (wikiPath?: string) =>
    wikiPath
      ? `/agent-sessions?wiki_path=${encodeURIComponent(wikiPath)}`
      : "/agent-sessions",

  // ── Triggers ──────────────────────────────────────────────────────────
  triggers: "/triggers",
  triggerDestinations: "/triggers/destinations",
  destinationConfigs: "/triggers/destination-configs",

  // ── Slack ─────────────────────────────────────────────────────────────
  slackConnectStatus: "/connectors/slack",

  // ── Users ─────────────────────────────────────────────────────────────
  adminUsers: "/admin/users",
  userSearch: (query: string) =>
    `/users/search?q=${encodeURIComponent(query.trim())}`,

  // ── Health ────────────────────────────────────────────────────────────
  health: "/health",

  // ── Activities ────────────────────────────────────────────────────────
  events: (opts: { kind?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams();
    if (opts.kind) qs.set("kind", opts.kind);
    if (opts.limit) qs.set("limit", String(opts.limit));
    return `/events${qs.toString() ? `?${qs}` : ""}`;
  },
} as const;
