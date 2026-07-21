"use client";

/** React hook that owns a page's live-session lifecycle + presence.
 *
 * The "co-edit session" is the page's *live session*: everyone viewing the
 * page joins it (presence + real-time updates); editing is a capability
 * inside it (`canWrite`), and presence labels participants "viewing" vs
 * "editing" by whether they currently have a caret placed in the text
 * (`caret_active` — position/intent, not edit facts; cleared on editor blur
 * or a hidden tab, never by a timer).
 *
 * On `enabled` it joins the session for `path`, streams inbound frames, and
 * exposes presence (`participants`/`typing`/`peers`) and autosave
 * (`saveStatus`). Teardown (flush → checkpoint → leave → stream abort, in
 * that order — leaving/disconnecting first would let the server's last-leave
 * forced commit race ahead of the final ops) happens entirely inside the
 * hook's own effect cleanup — driven by `enabled`/`path` changing or the
 * component unmounting, never by the caller invoking a function. The
 * *document* is owned by the editor via
 * `@codemirror/collab` (see `Coeditor`), not here — the hook just hands the
 * editor what it needs to run collab (`session` = id/clientId/start
 * version+doc), forwards inbound `op`/`resync` frames to it (`onServerFrame`),
 * and keeps a read-only `buffer` mirror for non-editor UI (the template
 * gallery).
 *
 * Autosave: every `reportDoc` (called by the editor on each doc change) arms
 * an idle timer (checkpoint `AUTOSAVE_IDLE_MS` after the last edit) and, if
 * not already running, a hard-cap timer (`AUTOSAVE_MAX_INTERVAL_MS`) so a
 * continuously-typing user still checkpoints periodically. There's no
 * explicit Save.
 *
 * If the join handshake itself fails, `session` stays null forever and
 * `joinError` is set — the editor has no read-only fallback to fall back to
 * now, so the caller must show `joinError` and offer `retryJoin` rather than
 * leaving the UI stuck on a permanent "Connecting…".
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

/** Manage the full lifecycle of a page's live session: join, stream,
 * presence, and save. Disable with `enabled: false` to leave the session and
 * tear down the stream. */
export function useCoeditSession(opts: {
  path: string;
  enabled: boolean;
  committedBody: string;
  myUserId: string | null;
  /** Whether the user may edit this page (`can_write` from the file read).
   * False joins the live session as a pure viewer: presence + real-time
   * updates flow as usual, but every write call (ops come from the read-only
   * editor anyway, cursor pings, checkpoints) is suppressed — the server
   * would 403 them. Defaults to true. */
  canWrite?: boolean;
  onEnd?: () => void;
}): UseCoeditSession {
  const {
    path,
    enabled,
    committedBody,
    myUserId,
    canWrite = true,
    onEnd,
  } = opts;

  const [buffer, setBuffer] = useState("");
  const [participants, setParticipants] = useState<CoeditParticipant[]>([]);
  const [typing, setTyping] = useState<string[]>([]);
  const [peers, setPeers] = useState<CoeditPeer[]>([]);
  const [active, setActive] = useState(false);
  const [session, setSession] = useState<CoeditSessionHandle | null>(null);
  const [joinError, setJoinError] = useState<string | null>(null);
  // Bumped by `retryJoin` to force the join effect to re-run even when
  // `enabled`/`path` haven't changed.
  const [retryToken, setRetryToken] = useState(0);
  const retryJoin = useCallback(() => {
    setJoinError(null);
    setRetryToken((t) => t + 1);
  }, []);
  const [saveStatus, setSaveStatus] =
    useState<UseCoeditSession["saveStatus"]>("saved");

  // Autosave timers: idle (reset on every edit) + a hard cap that fires
  // regardless, so continuous typing still checkpoints periodically.
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const maxIntervalTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const sessionId = useRef<number | null>(null);
  const clientId = useRef<string>("");
  const abort = useRef<AbortController | null>(null);
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
  // Client-local frame counter stamped onto peer entries (CoeditPeer.seq) so
  // the editor can tell fresh cursor frames from re-sent array entries.
  const frameSeq = useRef(0);
  // Roster mirror for synchronous display-name lookups (the op branch in
  // `onFrame` needs the author's display to restore their caret). Synced on
  // join and on presence frames — the only points membership changes.
  const participantsRef = useRef<CoeditParticipant[]>([]);
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
    if (sid === null || !canWrite) return;
    setSaveStatus("saving");
    try {
      await flushFn.current?.();
      await checkpointSession(sid);
      setSaveStatus("saved");
    } catch {
      setSaveStatus("error");
    }
  }, [canWrite]);

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
      // Cursor broadcasts are writes (the endpoint is write-gated); a pure
      // viewer's caret stays local.
      if (sessionId.current === null || !canWrite) return;
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
    [canWrite],
  );

  const reportCaretCleared = useCallback(() => {
    if (sessionId.current === null || !canWrite) return;
    // Drop any queued position first — a throttled send landing after the
    // clear would resurrect the caret.
    pendingCursor.current = null;
    lastCursor.current = null;
    if (typingIdle.current) {
      clearTimeout(typingIdle.current);
      typingIdle.current = null;
    }
    // Immediate (clears are rare) + keepalive so the hidden-tab clear
    // survives the page backgrounding.
    void sendCursor(sessionId.current, null, null, false, {
      keepalive: true,
    }).catch(() => {});
  }, [canWrite]);

  const onFrame = useCallback(
    (frame: CoeditFrame) => {
      if (frame.type === "presence") {
        participantsRef.current = frame.participants;
        setParticipants(frame.participants);
        const ids = new Set(frame.participants.map((p) => p.user_id));
        setPeers((prev) => prev.filter((p) => ids.has(p.user_id)));
        setTyping((prev) => prev.filter((u) => ids.has(u)));
        return;
      }
      if (frame.type === "cursor") {
        if (myUserId !== null && frame.user_id === myUserId) return;
        const uid = frame.user_id;
        const anchor = frame.anchor;
        const head = frame.head;
        if (anchor === null || head === null) {
          // The peer cleared their caret (editor blur / hidden tab).
          setPeers((prev) => prev.filter((p) => p.user_id !== uid));
        } else {
          frameSeq.current += 1;
          setPeers((prev) => [
            ...prev.filter((p) => p.user_id !== uid),
            {
              user_id: uid,
              user_display: frame.user_display,
              anchor,
              head,
              seq: frameSeq.current,
            },
          ]);
        }
        // Roster broadcasts only arrive on join/leave, so the "editing" /
        // "viewing" label rides the cursor frames themselves.
        const caretActive = anchor !== null && head !== null;
        setParticipants((prev) =>
          prev.map((p) =>
            p.user_id === uid && p.caret_active !== caretActive
              ? { ...p, caret_active: caretActive }
              : p,
          ),
        );
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
      // An applied edit implies caret placement (mirrors the server's /op
      // stamp), and the op itself says where: the end of its last change, in
      // post-edit coordinates. Restore both the label and the caret from it,
      // so a lost cursor frame can't leave a peer marked "editing" with no
      // caret rendered.
      if (frame.type === "op" && frame.author !== null) {
        const author = frame.author;
        setParticipants((prev) =>
          prev.map((p) =>
            p.user_id === author && !p.caret_active
              ? { ...p, caret_active: true }
              : p,
          ),
        );
        const display =
          myUserId !== null && author === myUserId
            ? undefined // never render self as a peer
            : participantsRef.current.find((p) => p.user_id === author)
                ?.user_display;
        if (display !== undefined && frame.changes.length > 0) {
          let delta = 0;
          let caret = 0;
          for (const c of frame.changes) {
            caret = c.from + delta + c.insert.length;
            delta += c.insert.length - (c.to - c.from);
          }
          frameSeq.current += 1;
          setPeers((prev) => [
            ...prev.filter((p) => p.user_id !== author),
            {
              user_id: author,
              user_display: display,
              anchor: caret,
              head: caret,
              seq: frameSeq.current,
            },
          ]);
        }
      }
      // op / resync → the editor's collab layer applies + rebases.
      serverFrame.current?.(frame);
    },
    [myUserId],
  );

  // Tears down the *local* session state (timers, presence, handles) and
  // fires `onEnd` — the one and only "session is over" signal. The network
  // side (flush, checkpoint, leave, stream abort) is NOT done here: the join
  // effect's cleanup below — the only caller — sequences those explicitly,
  // because their order matters (see the comment there).
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
    lastCursor.current = null;
    setTyping([]);
    setPeers([]);
    sessionId.current = null;
    setActive(false);
    setSession(null);
    onEnd?.();
  }, [clearAutosaveTimers, onEnd]);

  // Join + stream while enabled (the caller passes `!viewingVersion` — i.e.
  // whenever the live/current doc is showing, not an old commit); leave on
  // disable/path-change/unmount. `retryToken` lets `retryJoin` force a re-run
  // without `enabled`/`path` changing.
  useEffect(() => {
    if (!enabled) return;
    const ctrl = new AbortController();
    abort.current = ctrl;
    clientId.current = newClientId();
    let cancelled = false;
    // Mirror shows the committed body until the join resolves.
    setBuffer(committedBody);
    setJoinError(null);

    void (async () => {
      let snap;
      try {
        snap = await joinSession(path);
      } catch (e) {
        // The join handshake itself failed — there's no session to fall
        // back to (no more read-only mode), so this must be surfaced rather
        // than left as a silent, permanent "Connecting…".
        if (!cancelled) {
          setJoinError(
            e instanceof Error
              ? e.message
              : "Failed to join the editing session.",
          );
        }
        return;
      }
      if (cancelled) return;
      sessionId.current = snap.session_id;
      setBuffer(snap.buffer);
      participantsRef.current = snap.participants;
      setParticipants(snap.participants);
      setSession({
        id: snap.session_id,
        clientId: clientId.current,
        startVersion: snap.version,
        startDoc: snap.buffer,
      });
      setActive(true);
      try {
        // Gate on `cancelled`: the stream outlives the effect during teardown
        // (it's aborted only after the final flush/checkpoint/leave below), and
        // a late frame from the old session must not leak into state a new
        // session now owns.
        await streamSession(
          snap.session_id,
          (frame) => {
            if (!cancelled) onFrame(frame);
          },
          ctrl.signal,
        );
      } catch {
        // Stream ended/dropped after a successful join (including our own
        // cleanup's abort) — the session/doc are already usable, so this
        // isn't a join failure and doesn't need to surface as one.
      }
    })();

    return () => {
      cancelled = true;
      // Teardown must run flush → checkpoint → leave → abort, in that strict
      // order. The server force-commits and closes the session when the last
      // participant is gone, and BOTH leaving and dropping the SSE stream
      // count as "gone" — so leaving (or aborting) before the final ops and
      // checkpoint have landed lets that forced commit race ahead of them:
      // it commits a buffer missing the tail, `close_if_clean` closes the
      // session, and the late ops bounce off a closed session (silent loss).
      // Leaving last means the forced commit only ever sees a clean,
      // fully-flushed buffer.
      const sid = sessionId.current;
      // The editor never unregisters its flush (see Coeditor's cleanup) so
      // it's still here even when the child unmounted first; clear it as we
      // take ownership of the final call.
      const flush = flushFn.current;
      flushFn.current = null;
      const ctrl = abort.current;
      abort.current = null;
      stop();
      if (sid === null) {
        ctrl?.abort();
        return;
      }
      void (async () => {
        try {
          await flush?.();
        } catch {
          // Best-effort: the tail couldn't be delivered (offline, or a peer's
          // concurrent edit mid-teardown). Still checkpoint — committing what
          // the server has beats leaving it all to the forced commit.
        }
        if (canWrite) {
          try {
            await checkpointSession(sid, { keepalive: true });
          } catch {
            // The last-leave forced commit below is the backstop.
          }
        }
        try {
          await leaveSession(sid, { keepalive: true });
        } catch {
          // The server-side leave on SSE disconnect is the backstop.
        }
        ctrl?.abort();
      })();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, path, retryToken]);

  // Best-effort checkpoint when the tab is backgrounded/closed — `keepalive`
  // lets the request survive the page tearing down. Covers the gap between
  // the last edit and the idle-autosave timer firing. A hidden tab also
  // clears our caret: the user isn't positioned to edit anymore, and CM blur
  // doesn't fire reliably on tab switches.
  useEffect(() => {
    if (!active) return;
    const checkpointNow = () => {
      const sid = sessionId.current;
      if (sid === null || !canWrite) return;
      void checkpointSession(sid, { keepalive: true }).catch(() => {});
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        checkpointNow();
        reportCaretCleared();
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("pagehide", checkpointNow);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("pagehide", checkpointNow);
    };
  }, [active, canWrite, reportCaretCleared]);

  return {
    active,
    buffer,
    participants,
    typing,
    peers,
    session,
    joinError,
    retryJoin,
    saveStatus,
    onServerFrame,
    reportDoc,
    registerFlush,
    registerSetDoc,
    setDoc,
    reportSelection,
    reportCaretCleared,
  };
}
