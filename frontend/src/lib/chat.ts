import useSWR from "swr";

import { apiFetch, apiStream } from "@/lib/api";
import { SWR_KEYS } from "@/lib/swr-keys";

export interface ChatSession {
  id: string;
  title: string | null;
  /** Set when the list was requested with a page and this chat worked on it. */
  touches_path: boolean;
  created_at: string;
  updated_at: string;
}

export interface ChatStreamEventBase {
  type: string;
}

export type ChatFeedback = "up" | "down";

export interface PersistedChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  events: ChatStreamEventBase[] | null;
  feedback?: ChatFeedback | null;
  created_at: string;
}

export interface ChatSessionDetail {
  session: ChatSession;
  messages: PersistedChatMessage[];
}

export function createSession(): Promise<ChatSession> {
  return apiFetch<ChatSession>("/chat/sessions", { method: "POST" });
}

/** History-menu sessions. ``path`` marks rows that worked on that page,
 *  ``enabled`` gates the fetch, and the key is the request path so the
 *  global fetcher serves it. */
export function useChatSessions(path: string | null, enabled: boolean) {
  const { data } = useSWR<ChatSession[]>(
    enabled ? SWR_KEYS.chatSessions(path) : null,
  );
  return { sessions: data };
}

export function getSession(id: string): Promise<ChatSessionDetail> {
  return apiFetch<ChatSessionDetail>(
    `/chat/sessions/${encodeURIComponent(id)}`,
  );
}

/** Rate an assistant turn, or pass ``null`` to clear the rating. Ratings are
 *  message metadata and do not change later agent answers. */
export function setMessageFeedback(
  messageId: string,
  feedback: ChatFeedback | null,
): Promise<void> {
  return apiFetch<void>(
    `/chat/messages/${encodeURIComponent(messageId)}/feedback`,
    { method: "PUT", body: JSON.stringify({ feedback }) },
  );
}

export function streamMessage(
  sessionId: string,
  content: string,
  onEvent: (data: unknown) => void,
  options?: { signal?: AbortSignal; contextPaths?: string[] },
): Promise<void> {
  return apiStream(
    "/chat/messages",
    {
      method: "POST",
      // context_paths: the wiki pages on the composer's chips, so the agent
      // knows what the turn is about (empty when nothing is attached).
      body: JSON.stringify({
        session_id: sessionId,
        content,
        context_paths: options?.contextPaths ?? [],
      }),
    },
    onEvent,
    options?.signal,
  );
}

/** Bootstrap a hidden drafting session. Pass a ``templateId`` to seed
 *  the session from that template, or ``null`` to seed a generic
 *  "blank document" prime that hints at the wiki's auto-fill behavior.
 *  Streams the agent's kickoff turn. The first event is
 *  ``{type: "session_created", session_id: …}`` — the caller should
 *  pin subsequent ``streamMessage`` calls to that id. */
export function streamDraftingInit(
  templateId: string | null,
  onEvent: (data: unknown) => void,
  options?: { signal?: AbortSignal },
): Promise<void> {
  return apiStream(
    "/chat/drafting/init",
    {
      method: "POST",
      body: JSON.stringify({ template_id: templateId }),
    },
    onEvent,
    options?.signal,
  );
}
