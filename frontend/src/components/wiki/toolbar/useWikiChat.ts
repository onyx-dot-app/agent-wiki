"use client";

// Chat engine for the wiki AI toolbar. Rendering lives in ToolbarChat.
import { useCallback, useEffect, useRef, useState } from "react";

import {
  createSession,
  getSession,
  setMessageFeedback,
  streamDraftingInit,
  streamMessage,
  type ChatFeedback,
} from "@/lib/chat";
import {
  formatChatError,
  isThinking,
  itemsFromPersisted,
  markRunningToolsAsError,
  reduceEvent,
  setItemFeedback,
  type ChatItem,
  type StreamEvent,
} from "@/lib/chatState";
import { useDrafting } from "@/lib/drafting";
import { reviseDraft } from "@/lib/wiki/svc";

export interface WikiChat {
  items: ChatItem[];
  sending: boolean;
  thinking: boolean;
  error: string | null;
  send: (text: string) => void;
  stop: () => void;
  newSession: () => void;
  retry: () => void;
  rate: (messageId: string, feedback: ChatFeedback | null) => void;
  /** Replace the live conversation with a stored session's transcript. */
  loadSession: (id: string) => void;
}

interface WikiChatOptions {
  contextPaths: string[];
  /** Fired when drafting force-activates the chat (unfold the toolbar). */
  onActivate?: () => void;
}

interface ActiveOperation {
  id: number;
  controller: AbortController;
}

function feedbackForMessage(
  items: ChatItem[],
  messageId: string,
): ChatFeedback | null {
  for (const item of items) {
    if (item.kind === "assistant" && item.id === messageId) {
      return item.feedback ?? null;
    }
  }
  return null;
}

// Survives navigation: surfaces remount per page, so the active session id
// lives in sessionStorage and the transcript replays from the server.
const ACTIVE_SESSION_KEY = "wiki-chat:active-session";

function readActiveSession(): string | null {
  try {
    return window.sessionStorage.getItem(ACTIVE_SESSION_KEY);
  } catch {
    return null;
  }
}

function writeActiveSession(id: string | null): void {
  try {
    if (id === null) window.sessionStorage.removeItem(ACTIVE_SESSION_KEY);
    else window.sessionStorage.setItem(ACTIVE_SESSION_KEY, id);
  } catch {}
}

export function useWikiChat(opts: WikiChatOptions): WikiChat {
  const { drafting, expandTick, getDraftBody, applyDraftBody } = useDrafting();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [items, setItems] = useState<ChatItem[]>([]);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const operationRef = useRef(0);

  const beginOperation = useCallback((): ActiveOperation => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    operationRef.current += 1;
    return { id: operationRef.current, controller };
  }, []);

  const cancelOperation = useCallback(() => {
    operationRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  useEffect(() => cancelOperation, [cancelOperation]);

  const loadSessionRef = useRef<((id: string) => void) | null>(null);

  // Remounting restores the active conversation from the server. Gated on
  // empty state, not run-once, so StrictMode's discarded first pass
  // retries. An active drafting flow suppresses the restore.
  useEffect(() => {
    const { sessionId: sid, items: current } = chatStateRef.current;
    if (sid !== null && current.length > 0) return;
    if (drafting !== null) return;
    const saved = readActiveSession();
    if (saved) loadSessionRef.current?.(saved);
  }, [drafting]);

  // Drafting sessions are hidden scratch sessions the user can't browse to.
  const [draftingKey, setDraftingKey] = useState<string | null>(null);

  const onActivateRef = useRef(opts.onActivate);
  onActivateRef.current = opts.onActivate;

  // Unfold whenever the drafting context fires an expand request. Watch the
  // tick, not ``drafting``, so revisiting an already-drafting page doesn't
  // keep yanking the toolbar open.
  useEffect(() => {
    if (expandTick === 0) return;
    onActivateRef.current?.();
  }, [expandTick]);

  // A drafting identity change snapshots the chat and starts a hidden kickoff
  // stream. Clearing drafting restores the snapshot. The key distinguishes
  // templates, blank drafts, and deleted templates.
  const preDraftingRef = useRef<{
    sessionId: string | null;
    items: ChatItem[];
  } | null>(null);
  const chatStateRef = useRef({ draftingKey, sessionId, items });
  chatStateRef.current = { draftingKey, sessionId, items };
  const draftingRef = useRef(drafting);
  draftingRef.current = drafting;
  const desiredKey: string | null =
    drafting === null
      ? null
      : drafting.kind === "template"
        ? `tpl:${drafting.templateId ?? "deleted"}`
        : "blank";

  useEffect(() => {
    const activeDrafting = draftingRef.current;
    const current = chatStateRef.current;
    if (desiredKey !== null && activeDrafting !== null) {
      if (desiredKey === current.draftingKey) return;
      if (preDraftingRef.current === null) {
        preDraftingRef.current = {
          sessionId: current.sessionId,
          items: current.items,
        };
      }
      const operation = beginOperation();
      setDraftingKey(desiredKey);
      setError(null);
      setSessionId(null);
      onActivateRef.current?.();
      const tidForInit =
        activeDrafting.kind === "template" ? activeDrafting.templateId : null;
      setItems([]);
      setSending(true);
      void (async () => {
        try {
          await streamDraftingInit(
            tidForInit,
            (raw) => {
              if (
                operationRef.current !== operation.id ||
                operation.controller.signal.aborted
              )
                return;
              const ev = raw as StreamEvent;
              if (ev.type === "session_created") setSessionId(ev.session_id);
              else if (ev.type === "error") {
                setError(ev.message);
                setItems((prev) => markRunningToolsAsError(prev));
              } else setItems((prev) => reduceEvent(prev, ev));
            },
            { signal: operation.controller.signal },
          );
        } catch (e) {
          if (
            operationRef.current === operation.id &&
            !operation.controller.signal.aborted
          ) {
            setError(formatChatError(e));
            setItems((prev) => markRunningToolsAsError(prev));
          }
        } finally {
          if (operationRef.current === operation.id) {
            if (abortRef.current === operation.controller) {
              abortRef.current = null;
            }
            setSending(false);
          }
        }
      })();
      return () => operation.controller.abort();
    }

    // Drafting ended: revert conversation in one render.
    if (current.draftingKey === null) return;
    cancelOperation();
    const snapshot = preDraftingRef.current;
    preDraftingRef.current = null;
    setDraftingKey(null);
    setSessionId(snapshot?.sessionId ?? null);
    setItems(snapshot?.items ?? []);
    setError(null);
    setSending(false);
  }, [beginOperation, cancelOperation, desiredKey]);

  const send = useCallback(
    (text: string) => {
      if (sending || abortRef.current !== null) return;
      const operation = beginOperation();
      setError(null);
      setSending(true);
      // Optimistic user turn. ``reduceEvent`` opens the assistant bubble on
      // the first text_delta.
      setItems((prev) => [
        ...prev,
        { kind: "user", content: text, createdAt: new Date().toISOString() },
      ]);

      void (async () => {
        // Unsaved new-doc drafting with content in the editor: live-edit it
        // via the stateless reviser, no chat session involved.
        const body = getDraftBody();
        if (
          drafting?.kind === "blank" &&
          drafting.path === null &&
          body !== null &&
          body.trim() !== ""
        ) {
          try {
            const res = await reviseDraft(body, text);
            if (
              operationRef.current !== operation.id ||
              operation.controller.signal.aborted
            )
              return;
            applyDraftBody(res.body);
            setItems((prev) => [
              ...prev,
              {
                kind: "assistant",
                content: "Done. I've updated the draft in the editor.",
              },
            ]);
          } catch (err) {
            if (
              operationRef.current === operation.id &&
              !operation.controller.signal.aborted
            ) {
              setError(formatChatError(err));
            }
          } finally {
            if (operationRef.current === operation.id) {
              if (abortRef.current === operation.controller) {
                abortRef.current = null;
              }
              setSending(false);
            }
          }
          return;
        }

        // Lazy session creation so empty sessions don't pile up.
        let activeId = sessionId;
        if (!activeId) {
          try {
            activeId = (await createSession()).id;
            if (operationRef.current !== operation.id) return;
            if (operation.controller.signal.aborted) {
              if (abortRef.current === operation.controller) {
                abortRef.current = null;
              }
              setSending(false);
              return;
            }
            setSessionId(activeId);
            writeActiveSession(activeId);
          } catch (e) {
            if (
              operationRef.current !== operation.id ||
              operation.controller.signal.aborted
            )
              return;
            setError(formatChatError(e));
            setSending(false);
            setItems((prev) => prev.slice(0, -1));
            if (abortRef.current === operation.controller) {
              abortRef.current = null;
            }
            return;
          }
        }

        let streamFailed = false;
        try {
          await streamMessage(
            activeId,
            text,
            (raw) => {
              if (
                operationRef.current !== operation.id ||
                operation.controller.signal.aborted
              )
                return;
              const ev = raw as StreamEvent;
              if (ev.type === "error") {
                streamFailed = true;
                setError(ev.message);
                setItems((prev) => markRunningToolsAsError(prev));
                return;
              }
              setItems((prev) => reduceEvent(prev, ev));
            },
            {
              contextPaths: opts.contextPaths,
              signal: operation.controller.signal,
            },
          );
        } catch (err) {
          if (operationRef.current !== operation.id) return;
          if (!operation.controller.signal.aborted) {
            streamFailed = true;
            setError(formatChatError(err));
          }
          setItems((prev) => markRunningToolsAsError(prev));
        } finally {
          if (operationRef.current !== operation.id) return;
          if (abortRef.current === operation.controller)
            abortRef.current = null;
          setSending(false);
          if (streamFailed) {
            // Drop a trailing empty assistant bubble the reducer never filled.
            setItems((prev) => {
              const last = prev[prev.length - 1];
              return last && last.kind === "assistant" && last.content === ""
                ? prev.slice(0, -1)
                : prev;
            });
          }
        }
      })();
    },
    [
      applyDraftBody,
      beginOperation,
      drafting,
      getDraftBody,
      opts.contextPaths,
      sending,
      sessionId,
    ],
  );

  const stop = useCallback(() => {
    cancelOperation();
    setItems((prev) => markRunningToolsAsError(prev));
    setSending(false);
  }, [cancelOperation]);

  const newSession = useCallback(() => {
    cancelOperation();
    writeActiveSession(null);
    setSessionId(null);
    setItems([]);
    setError(null);
    setSending(false);
  }, [cancelOperation]);

  const rate = useCallback(
    (messageId: string, feedback: ChatFeedback | null) => {
      const previousFeedback = feedbackForMessage(itemsRef.current, messageId);
      // Optimistic: the rating is advisory, so a failed write reverts the
      // button rather than interrupting the conversation with an error.
      setItems((prev) => setItemFeedback(prev, messageId, feedback));
      void setMessageFeedback(messageId, feedback).catch(() => {
        setItems((prev) => {
          const currentFeedback = feedbackForMessage(prev, messageId);
          return currentFeedback === feedback
            ? setItemFeedback(prev, messageId, previousFeedback)
            : prev;
        });
      });
    },
    [],
  );

  const retry = useCallback(() => {
    const lastUser = itemsRef.current.findLast((item) => item.kind === "user");
    if (!lastUser || sending) return;
    const text = lastUser.content;
    // Drop the failed turn from the last user row on. send() re-appends
    // the user row, and the server reuses its persisted copy.
    setItems((prev) => {
      const lastUser = prev.map((i) => i.kind).lastIndexOf("user");
      return lastUser >= 0 ? prev.slice(0, lastUser) : prev;
    });
    send(text);
  }, [send, sending]);

  const loadSession = useCallback(
    (id: string) => {
      const operation = beginOperation();
      setError(null);
      setSending(true);
      void getSession(id)
        .then((detail) => {
          if (
            operationRef.current !== operation.id ||
            operation.controller.signal.aborted
          )
            return;
          setSessionId(id);
          writeActiveSession(id);
          setItems(itemsFromPersisted(detail.messages));
        })
        .catch((e) => {
          if (
            operationRef.current !== operation.id ||
            operation.controller.signal.aborted
          )
            return;
          setError(formatChatError(e));
          if (chatStateRef.current.sessionId === null) {
            writeActiveSession(null);
          }
        })
        .finally(() => {
          if (operationRef.current !== operation.id) return;
          if (abortRef.current === operation.controller) {
            abortRef.current = null;
          }
          setSending(false);
        });
    },
    [beginOperation],
  );
  loadSessionRef.current = loadSession;

  return {
    items,
    sending,
    thinking: sending && isThinking(items),
    error,
    send,
    stop,
    newSession,
    retry,
    rate,
    loadSession,
  };
}
