import { apiFetch, apiStream } from "@/lib/api";

export interface ChatSession {
  id: string;
  title: string | null;
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

export function listSessions(): Promise<ChatSession[]> {
  return apiFetch<ChatSession[]>("/chat/sessions");
}

export function createSession(): Promise<ChatSession> {
  return apiFetch<ChatSession>("/chat/sessions", { method: "POST" });
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

export function deleteSession(id: string): Promise<void> {
  return apiFetch<void>(`/chat/sessions/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function streamMessage(
  sessionId: string,
  content: string,
  onEvent: (data: unknown) => void,
  options?: { signal?: AbortSignal; currentPath?: string | null },
): Promise<void> {
  return apiStream(
    "/chat/messages",
    {
      method: "POST",
      // current_path: the wiki page the user has open, so the agent knows
      // what they're looking at (null when not on a page).
      body: JSON.stringify({
        session_id: sessionId,
        content,
        current_path: options?.currentPath ?? null,
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
  options?: { signal?: AbortSignal; prompt?: string | null },
): Promise<void> {
  return apiStream(
    "/chat/drafting/init",
    {
      method: "POST",
      body: JSON.stringify({
        template_id: templateId,
        prompt: options?.prompt ?? null,
      }),
    },
    onEvent,
    options?.signal,
  );
}
