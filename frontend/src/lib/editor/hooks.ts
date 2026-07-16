"use client";

/** React hook that owns a live co-edit session's lifecycle + presence.
 *
 * On `enabled` it joins the session for `path`, streams inbound frames, and
 * exposes presence (`participants`/`typing`/`peers`), autosave (`saveStatus`),
 * and a `leave` (checkpoint + leave, for the caller's own unmount/transition
 * cleanup). The *document* is owned by the editor via `@codemirror/collab`
 * (see `Coeditor`), not here — the hook just hands the editor what it needs to
 * run collab (`session` = id/clientId/start version+doc), forwards inbound
 * `op`/`resync` frames to it (`onServerFrame`), and keeps a read-only `buffer`
 * mirror for non-editor UI (the template gallery).
 *
 * Autosave: every `reportDoc` (called by the editor on each doc change) arms
 * an idle timer (checkpoint `AUTOSAVE_IDLE_MS` after the last edit) and, if
 * not already running, a hard-cap timer (`AUTOSAVE_MAX_INTERVAL_MS`) so a
 * continuously-typing user still checkpoints periodically. There's no
 * explicit Save — `leave` only runs on unmount/path-change/entering history.
 *
 * Offsets are UTF-16 (JS-native), matching the server — see `svc.ts`.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type {
  CoeditFrame,
  CoeditParticipant,
  CoeditPeer,
  UseCoeditSession,
  CoeditSessionHandle,
} from "@/lib/editor/types";
import {
  checkpointSession,
  joinSession,
  leaveSession,
  sendCursor,
  streamSession,
} from "@/lib/editor/svc";
import {
  AUTOSAVE_IDLE_MS,
  AUTOSAVE_MAX_INTERVAL_MS,
  CURSOR_THROTTLE_MS,
  TYPING_EXPIRY_MS,
  TYPING_IDLE_MS,
} from "@/lib/editor/constants";

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
  const [saveStatus, setSaveStatus] =
    useState<UseCoeditSession["saveStatus"]>("saved");

  // Autosave timers: idle (reset on every edit) + a hard cap that fires
  // regardless, so continuous typing still checkpoints periodically.
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const maxIntervalTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  const clearAutosaveTimers = useCallback(() => {
    if (idleTimer.current) {
      clearTimeout(idleTimer.current);
      idleTimer.current = null;
    }
    if (maxIntervalTimer.current) {
      clearTimeout(maxIntervalTimer.current);
      maxIntervalTimer.current = null;
    }
  }, []);

  // Flush + checkpoint without leaving the session — the autosave action.
  const checkpoint = useCallback(async () => {
    const sid = sessionId.current;
    if (sid === null) return;
    setSaveStatus("saving");
    try {
      await flushFn.current?.();
      await checkpointSession(sid);
      setSaveStatus("saved");
    } catch {
      setSaveStatus("error");
    }
  }, []);

  const runAutoCheckpoint = useCallback(() => {
    clearAutosaveTimers();
    void checkpoint();
  }, [clearAutosaveTimers, checkpoint]);

  const armAutosave = useCallback(() => {
    if (idleTimer.current) clearTimeout(idleTimer.current);
    idleTimer.current = setTimeout(runAutoCheckpoint, AUTOSAVE_IDLE_MS);
    if (!maxIntervalTimer.current) {
      maxIntervalTimer.current = setTimeout(
        runAutoCheckpoint,
        AUTOSAVE_MAX_INTERVAL_MS,
      );
    }
  }, [runAutoCheckpoint]);

  const reportDoc = useCallback(
    (doc: string) => {
      setBuffer(doc);
      armAutosave();
    },
    [armAutosave],
  );

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
    clearAutosaveTimers();
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
  }, [clearAutosaveTimers]);

  // Flush + checkpoint + leave the session. Called on unmount, on a path
  // change, and when switching into history view — never from a button.
  const leave = useCallback(async () => {
    if (sessionId.current === null && joinPromise.current) {
      await joinPromise.current;
    }
    const sid = sessionId.current;
    if (sid === null) return;
    // Flush the editor's un-acked ops so every keystroke reaches the server
    // before we checkpoint the buffer to git. If the flush fails (network),
    // let it throw: don't checkpoint a stale buffer or leave the session —
    // the caller surfaces the error and the user stays in the editor to retry.
    setSaveStatus("saving");
    await flushFn.current?.();
    try {
      await checkpointSession(sid);
      setSaveStatus("saved");
    } finally {
      stop();
      onEnd?.();
    }
  }, [stop, onEnd]);

  // Join + stream while enabled (the caller passes `!viewingVersion` — i.e.
  // whenever the live/current doc is showing, not an old commit); leave on
  // disable/path-change/unmount.
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
      // Capture before the synchronous `stop()` below clears them, so a
      // trailing checkpoint can still cover the last idle-autosave window
      // (the periodic autosave while mounted is the primary durability path;
      // this is best-effort for the tail end of it, e.g. a path change or
      // unmount mid-edit — small race against `stop()`'s own `leaveSession`
      // call, not worth blocking teardown to sequence perfectly).
      const sid = sessionId.current;
      const flush = flushFn.current;
      stop();
      if (sid !== null) {
        void (async () => {
          try {
            await flush?.();
          } catch {
            return;
          }
          void checkpointSession(sid, { keepalive: true }).catch(() => {});
        })();
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, path]);

  // Best-effort checkpoint when the tab is backgrounded/closed — `keepalive`
  // lets the request survive the page tearing down. Covers the gap between
  // the last edit and the idle-autosave timer firing.
  useEffect(() => {
    if (!active) return;
    const checkpointNow = () => {
      const sid = sessionId.current;
      if (sid === null) return;
      void checkpointSession(sid, { keepalive: true }).catch(() => {});
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") checkpointNow();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("pagehide", checkpointNow);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("pagehide", checkpointNow);
    };
  }, [active]);

  return {
    active,
    buffer,
    participants,
    typing,
    peers,
    session,
    saveStatus,
    onServerFrame,
    reportDoc,
    registerFlush,
    registerSetDoc,
    setDoc,
    reportSelection,
    leave,
  };
}
