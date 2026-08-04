// Pure chat-transcript state shared by every chat surface (toolbar, side
// panel). Reduces the server's stream events into renderable items. No
// React, no network.
import { ApiError } from "@/lib/api";
import type { ChatFeedback, PersistedChatMessage } from "@/lib/chat";
import {
  EDIT_TOOLS,
  SEARCH_TOOLS,
  SOURCE_TOOLS,
  presentTool,
} from "@/lib/tools";

export type ToolState = "running" | "done" | "error";

export type ChatItem =
  | { kind: "user"; content: string; createdAt?: string }
  // id arrives with ``message_saved`` once the turn is persisted, and is
  // what rating addresses. createdAt on the assistant marks the first
  // token, so the gap to the user turn is the thinking duration.
  | {
      kind: "assistant";
      content: string;
      id?: string;
      feedback?: ChatFeedback | null;
      createdAt?: string;
    }
  // detail: the human-meaningful argument (search query, path, question)
  // shown in the thinking-state chips next to the tool's label.
  | {
      kind: "tool";
      id: string;
      name: string;
      state: ToolState;
      detail?: string;
    };

export type StreamEvent =
  | { type: "text_delta"; text: string }
  | {
      type: "tool_call";
      id: string;
      name: string;
      arguments: Record<string, unknown>;
    }
  | { type: "tool_result"; id: string; name: string; content: string }
  | { type: "iteration_done" }
  | { type: "done" }
  | { type: "error"; code: string; message: string }
  | { type: "session_created"; session_id: string }
  | { type: "message_saved"; id: string };

export function reduceEvent(items: ChatItem[], ev: StreamEvent): ChatItem[] {
  if (ev.type === "text_delta") {
    const last = items[items.length - 1];
    if (last && last.kind === "assistant") {
      const next = items.slice();
      next[next.length - 1] = { ...last, content: last.content + ev.text };
      return next;
    }
    return [
      ...items,
      { kind: "assistant", content: ev.text, createdAt: nowIso() },
    ];
  }
  if (ev.type === "tool_call") {
    // Hidden tools (e.g. ``load_skill`` plumbing) never enter the
    // transcript. A later ``tool_result`` for the same id will no-op
    // through the ``map`` below, so we don't need a second guard.
    if (presentTool(ev.name).hidden) return items;
    return [
      ...items,
      {
        kind: "tool",
        id: ev.id,
        name: ev.name,
        state: "running",
        detail: toolDetail(ev.arguments),
      },
    ];
  }
  if (ev.type === "tool_result") {
    return items.map((it) =>
      it.kind === "tool" && it.id === ev.id ? { ...it, state: "done" } : it,
    );
  }
  if (ev.type === "message_saved") {
    // A turn can end on a tool row, so the id belongs to the last assistant
    // bubble rather than the last item. Rating is gated on that id.
    const at = items.findLastIndex((i) => i.kind === "assistant");
    const target = at === -1 ? null : items[at];
    if (!target || target.kind !== "assistant") return items;
    const next = items.slice();
    next[at] = { ...target, id: ev.id };
    return next;
  }
  return items;
}

/** Apply a rating to the turn the id addresses. */
export function setItemFeedback(
  items: ChatItem[],
  messageId: string,
  feedback: ChatFeedback | null,
): ChatItem[] {
  return items.map((it) =>
    it.kind === "assistant" && it.id === messageId ? { ...it, feedback } : it,
  );
}

// Convert persisted messages into ChatItems and replay assistant event logs.
// This restores tool calls inline. Legacy rows without events become one
// assistant bubble.
export function itemsFromPersisted(
  messages: PersistedChatMessage[],
): ChatItem[] {
  let out: ChatItem[] = [];
  for (const m of messages) {
    if (m.role === "user") {
      out = [
        ...out,
        { kind: "user", content: m.content, createdAt: m.created_at },
      ];
      continue;
    }
    if (!m.events || m.events.length === 0) {
      out = [
        ...out,
        {
          kind: "assistant",
          content: m.content,
          id: m.id,
          feedback: m.feedback ?? null,
        },
      ];
      continue;
    }
    for (const raw of m.events) {
      out = reduceEvent(out, raw as StreamEvent);
    }
    // The event log has no id or timestamp of its own, so stamp the row's
    // onto the bubble it just replayed. Without the createdAt override a
    // replayed bubble carries replay-time, not turn-time.
    out = reduceEvent(out, { type: "message_saved", id: m.id });
    out = setItemFeedback(out, m.id, m.feedback ?? null);
    out = out.map((it) =>
      it.kind === "assistant" && it.id === m.id
        ? { ...it, createdAt: m.created_at }
        : it,
    );
  }
  return out;
}

// True while there's no live signal yet: no text streaming into an
// assistant bubble and no tool running. Drives the "Thinking…" shimmer.
export function isThinking(items: ChatItem[]): boolean {
  const last = items[items.length - 1];
  if (!last) return true;
  if (last.kind === "assistant" && last.content !== "") return false;
  if (last.kind === "tool" && last.state === "running") return false;
  return true;
}

// When the stream fails mid-flight, any tool whose ``tool_result`` we
// never received is stuck "running". Flip those to "error" so the
// transcript doesn't lie about ongoing activity.
export function markRunningToolsAsError(items: ChatItem[]): ChatItem[] {
  return items.map((it) =>
    it.kind === "tool" && it.state === "running"
      ? { ...it, state: "error" }
      : it,
  );
}

// The human-meaningful argument of a tool call, when one exists.
function toolDetail(args: Record<string, unknown>): string | undefined {
  for (const key of ["query", "question", "path", "prompt"]) {
    const v = args[key];
    if (typeof v === "string" && v.trim()) return v;
  }
  return undefined;
}

// Search terms for the thinking state's chips ("Onyx", "Onyx features").
// Only searching tools qualify: the pages a turn read surface as source
// chips, so including them here would show a path under a search icon.
export function queryChipsFromItems(items: ChatItem[]): string[] {
  const out: string[] = [];
  for (const it of items) {
    if (it.kind !== "tool" || !SEARCH_TOOLS.has(it.name)) continue;
    const query = it.detail ?? presentTool(it.name).label;
    if (!out.includes(query)) out.push(query);
  }
  return out;
}

function nowIso(): string {
  return new Date().toISOString();
}

// Doc paths a turn's tools touched. detail carries the path argument for
// these tools (toolDetail's extraction order).
function toolPaths(items: ChatItem[], names: Set<string>): string[] {
  const out: string[] = [];
  for (const it of items) {
    if (it.kind !== "tool" || !names.has(it.name) || !it.detail) continue;
    if (!out.includes(it.detail)) out.push(it.detail);
  }
  return out;
}

export function sourcesFromItems(items: ChatItem[]): string[] {
  return toolPaths(items, SOURCE_TOOLS);
}

export function editsFromItems(items: ChatItem[]): string[] {
  return toolPaths(items, EDIT_TOOLS);
}

/** Seconds between a user turn and the reply that followed, for the
 *  "Thought for Ns" header. Retries land fresh replies under old user
 *  rows, so pairings beyond a plausible thinking window hide. */
export function thinkingSeconds(
  items: ChatItem[],
  assistantIndex: number,
): number | null {
  const a = items[assistantIndex];
  if (!a || a.kind !== "assistant" || !a.createdAt) return null;
  for (let i = assistantIndex - 1; i >= 0; i--) {
    const it = items[i];
    if (it.kind === "user") {
      if (!it.createdAt) return null;
      const s = (Date.parse(a.createdAt) - Date.parse(it.createdAt)) / 1000;
      if (!Number.isFinite(s) || s < 0 || s > 600) return null;
      return Math.round(s);
    }
  }
  return null;
}

export function formatChatError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 503) return `${err.message} (server status 503)`;
    if (err.status === 429) return `${err.message} (rate limited)`;
    if (err.status === 504 || err.status === 502)
      return `${err.message} (provider error ${err.status})`;
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return "Failed to send message";
}
