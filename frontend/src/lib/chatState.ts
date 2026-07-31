// Pure chat-transcript state shared by every chat surface (toolbar, side
// panel). Reduces the server's stream events into renderable items. No
// React, no network.
import { ApiError } from "@/lib/api";
import type { ChatFeedback, PersistedChatMessage } from "@/lib/chat";
import { presentTool } from "@/lib/tools";

export type ToolState = "running" | "done" | "error";

export type ChatItem =
  | { kind: "user"; content: string }
  // id arrives with ``message_saved`` once the turn is persisted, and is
  // what rating the turn addresses. Unrated turns carry feedback null.
  | {
      kind: "assistant";
      content: string;
      id?: string;
      feedback?: ChatFeedback | null;
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
    return [...items, { kind: "assistant", content: ev.text }];
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
    const last = items[items.length - 1];
    if (!last || last.kind !== "assistant") return items;
    const next = items.slice();
    next[next.length - 1] = { ...last, id: ev.id };
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
      out = [...out, { kind: "user", content: m.content }];
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
    // The event log has no id of its own, so stamp the row's onto the
    // bubble it just replayed to keep the turn rateable after a reload.
    out = reduceEvent(out, { type: "message_saved", id: m.id });
    out = setItemFeedback(out, m.id, m.feedback ?? null);
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

// Chip strings for the thinking state ("Onyx", "Onyx features", +N more):
// the tool's argument when it has one, its label otherwise.
export function queryChipsFromItems(items: ChatItem[]): string[] {
  const out: string[] = [];
  for (const it of items) {
    if (it.kind !== "tool") continue;
    out.push(it.detail ?? presentTool(it.name).label);
  }
  return out;
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
