"use client";

/** React hook that owns a live co-edit session's lifecycle + presence.
 *
 * On `enabled` it joins the session for `path`, streams inbound frames, and
 * exposes presence (`participants`/`typing`/`peers`) and a `save` (checkpoint +
 * leave). The *document* is owned by the editor via `@codemirror/collab` (see
 * `CoeditEditor`), not here — the hook just hands the editor what it needs to
 * run collab (`session` = id/clientId/start version+doc), forwards inbound
 * `op`/`resync` frames to it (`onServerFrame`), and keeps a read-only `buffer`
 * mirror for non-editor UI (the template gallery, the optimistic Done render).
 *
 * Offsets are UTF-16 (JS-native), matching the server — see `svc.ts`.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { CoeditFrame, CoeditParticipant, CoeditPeer, UseCoeditSession, CoeditSessionHandle } from "@/lib/coeditor/types";
import {
  checkpointSession,
  joinSession,
  leaveSession,
  sendCursor,
  streamSession,
} from "@/lib/coeditor/svc";
import { CURSOR_THROTTLE_MS, TYPING_EXPIRY_MS, TYPING_IDLE_MS } from "@/lib/coeditor/constants";

/** Generate a fresh per-connection collab clientId (UUID v4).
 * Used to filter out our own op echoes from the SSE stream. */
function newClientId(): string {
  return crypto.randomUUID();
}

/** Manage the full lifecycle of a co-edit session: join, stream, presence, and save.
 * Disable with `enabled: false` to leave the session and tear down the stream. */
export function useCoeditSession(opts: {
  path: string;
  enabled: boolean;
  committedBody: string;
  myUserId: string | null;
  onEnd?: () => void;
}): UseCoeditSession {
  const { path, enabled, committedBody, myUserId, onEnd } = opts;

  const [buffer, setBuffer] = useState("");
  const [participants, setParticipants] = useState<CoeditParticipant[]>([]);
  const [typing, setTyping] = useState<string[]>([]);
  const [peers, setPeers] = useState<CoeditPeer[]>([]);
  const [active, setActive] = useState(false);
  const [session, setSession] = useState<CoeditSessionHandle | null>(null);

  const sessionId = useRef<number | null>(null);
  const clientId = useRef<string>("");
  const abort = useRef<AbortController | null>(null);
  const joinPromise = useRef<Promise<void> | null>(null);
  // Editor-registered hooks: inbound frame handler + pending-op flush.
  const serverFrame = useRef<((frame: CoeditFrame) => void) | null>(null);
  const flushFn = useRef<(() => Promise<void>) | null>(null);
  const setDocFn = useRef<((text: string) => void) | null>(null);
  // Outbound cursor/typing throttle state.
  const cursorThrottle = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingCursor = useRef<{
    anchor: number;
    head: number;
    typing: boolean;
  } | null>(null);
  const lastCursor = useRef<{ anchor: number; head: number } | null>(null);
  const typingIdle = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Inbound: per-peer expiry timers so a silent "typing" peer clears.
  const typingTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(
    new Map(),
  );

  const onServerFrame = useCallback(
    (handler: ((frame: CoeditFrame) => void) | null) => {
      serverFrame.current = handler;
    },
    [],
  );
  const registerFlush = useCallback((fn: (() => Promise<void>) | null) => {
    flushFn.current = fn;
  }, []);
  const registerSetDoc = useCallback((fn: ((text: string) => void) | null) => {
    setDocFn.current = fn;
  }, []);
  const setDoc = useCallback((text: string) => setDocFn.current?.(text), []);
  const reportDoc = useCallback((doc: string) => setBuffer(doc), []);

  const reportSelection = useCallback(
    (anchor: number, head: number, isEdit: boolean) => {
      if (sessionId.current === null) return;
      lastCursor.current = { anchor, head };
      // A caret move (isEdit=false) must not clobber the "typing…" a recent edit
      // set — browsers fire `select` right after every `input`, so onSelect
      // lands one keystroke behind onChange. Derive typing from the idle timer.
      const isTyping = isEdit || typingIdle.current !== null;
      pendingCursor.current = { anchor, head, typing: isTyping };
      const send = () => {
        const c = pendingCursor.current;
        pendingCursor.current = null;
        if (c && sessionId.current !== null) {
          void sendCursor(sessionId.current, c.anchor, c.head, c.typing).catch(
            () => {},
          );
        }
      };
      if (!cursorThrottle.current) {
        send();
        cursorThrottle.current = setTimeout(() => {
          cursorThrottle.current = null;
          if (pendingCursor.current) send();
        }, CURSOR_THROTTLE_MS);
      }
      if (isEdit) {
        if (typingIdle.current) clearTimeout(typingIdle.current);
        typingIdle.current = setTimeout(() => {
          typingIdle.current = null;
          const lc = lastCursor.current;
          if (sessionId.current !== null && lc) {
            void sendCursor(sessionId.current, lc.anchor, lc.head, false).catch(
              () => {},
            );
          }
        }, TYPING_IDLE_MS);
      }
    },
    [],
  );

  const onFrame = useCallback(
    (frame: CoeditFrame) => {
      if (frame.type === "presence") {
        setParticipants(frame.participants);
        const ids = new Set(frame.participants.map((p) => p.user_id));
        setPeers((prev) => prev.filter((p) => ids.has(p.user_id)));
        setTyping((prev) => prev.filter((u) => ids.has(u)));
        return;
      }
      if (frame.type === "cursor") {
        if (myUserId !== null && frame.user_id === myUserId) return;
        const uid = frame.user_id;
        setPeers((prev) => [
          ...prev.filter((p) => p.user_id !== uid),
          {
            user_id: uid,
            user_display: frame.user_display,
            anchor: frame.anchor,
            head: frame.head,
          },
        ]);
        const existing = typingTimers.current.get(uid);
        if (existing) clearTimeout(existing);
        typingTimers.current.delete(uid);
        if (frame.typing) {
          setTyping((prev) => (prev.includes(uid) ? prev : [...prev, uid]));
          typingTimers.current.set(
            uid,
            setTimeout(() => {
              typingTimers.current.delete(uid);
              setTyping((prev) => prev.filter((u) => u !== uid));
            }, TYPING_EXPIRY_MS),
          );
        } else {
          setTyping((prev) => prev.filter((u) => u !== uid));
        }
        return;
      }
      // op / resync → the editor's collab layer applies + rebases.
      serverFrame.current?.(frame);
    },
    [myUserId],
  );

  const stop = useCallback(() => {
    if (cursorThrottle.current) {
      clearTimeout(cursorThrottle.current);
      cursorThrottle.current = null;
    }
    if (typingIdle.current) {
      clearTimeout(typingIdle.current);
      typingIdle.current = null;
    }
    for (const t of typingTimers.current.values()) clearTimeout(t);
    typingTimers.current.clear();
    pendingCursor.current = null;
    setTyping([]);
    setPeers([]);
    abort.current?.abort();
    abort.current = null;
    const sid = sessionId.current;
    sessionId.current = null;
    setActive(false);
    setSession(null);
    if (sid !== null) void leaveSession(sid).catch(() => {});
  }, []);

  const save = useCallback(async () => {
    if (sessionId.current === null && joinPromise.current) {
      await joinPromise.current;
    }
    const sid = sessionId.current;
    if (sid === null) return;
    // Flush the editor's un-acked ops so every keystroke reaches the server
    // before we checkpoint the buffer to git. If the flush fails (network),
    // let it throw: don't checkpoint a stale buffer or leave the session —
    // the caller surfaces the error and the user stays in the editor to retry.
    await flushFn.current?.();
    try {
      await checkpointSession(sid);
    } finally {
      stop();
      onEnd?.();
    }
  }, [stop, onEnd]);

  // Join + stream while enabled; leave on disable/unmount.
  useEffect(() => {
    if (!enabled) return;
    const ctrl = new AbortController();
    abort.current = ctrl;
    clientId.current = newClientId();
    let cancelled = false;
    // Mirror shows the committed body until the join resolves.
    setBuffer(committedBody);

    const run = (async () => {
      try {
        const snap = await joinSession(path);
        if (cancelled) return;
        sessionId.current = snap.session_id;
        setBuffer(snap.buffer);
        setParticipants(snap.participants);
        setSession({
          id: snap.session_id,
          clientId: clientId.current,
          startVersion: snap.version,
          startDoc: snap.buffer,
        });
        setActive(true);
        await streamSession(snap.session_id, onFrame, ctrl.signal);
      } catch {
        // join failed or stream ended; leave edit mode gracefully
      }
    })();
    joinPromise.current = run;

    return () => {
      cancelled = true;
      joinPromise.current = null;
      stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, path]);

  return {
    active,
    buffer,
    participants,
    typing,
    peers,
    session,
    onServerFrame,
    reportDoc,
    registerFlush,
    registerSetDoc,
    setDoc,
    reportSelection,
    save,
  };
}
