/** React hook that binds a text buffer to a live co-edit session.
 *
 * On `enabled`, it joins the session for `path`, streams inbound frames, and
 * exposes `buffer` + `onChange` for a plain `<textarea>`. Local edits are sent
 * as coalesced range-change ops (one in flight); inbound ops are spliced in;
 * anything it can't cleanly reconcile (a 409 stale op, a `resync` frame, or a
 * remote op arriving while the local buffer has unsent edits) falls back to a
 * full re-fetch — correct, occasionally jumpy (the textarea MVP; CodeMirror +
 * pending-op rebase smooth this later). Save checkpoints + leaves; discard
 * resets to the committed body + leaves; unmount leaves.
 *
 * Offsets are UTF-16 (JS-native), matching the server — see `coedit.ts`.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "./api";
import {
  applyChange,
  checkpointSession,
  type CoeditFrame,
  type CoeditParticipant,
  diffToChange,
  getSession,
  joinSession,
  leaveSession,
  sendOp,
  streamSession,
} from "./coedit";

const FLUSH_DELAY_MS = 150;

export interface UseCoeditSession {
  active: boolean;
  buffer: string;
  participants: CoeditParticipant[];
  onChange: (next: string) => void;
  save: () => Promise<void>;
  discard: () => Promise<void>;
}

export function useCoeditSession(opts: {
  path: string;
  enabled: boolean;
  committedBody: string;
  myUserId: string | null;
  onEnd?: () => void;
}): UseCoeditSession {
  const { path, enabled, committedBody, myUserId, onEnd } = opts;

  const [buffer, setBufferState] = useState("");
  const [participants, setParticipants] = useState<CoeditParticipant[]>([]);
  const [active, setActive] = useState(false);

  // Live state kept in refs so the stream callback and flush loop read the
  // latest without re-subscribing.
  const sessionId = useRef<number | null>(null);
  const version = useRef(0);
  const serverBuffer = useRef(""); // last text the server has acked
  const bufferRef = useRef(""); // mirrors `buffer` state for diffing
  const pumpPromise = useRef<Promise<void> | null>(null); // in-flight op drain
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abort = useRef<AbortController | null>(null);
  const joinPromise = useRef<Promise<void> | null>(null); // in-flight join

  const setBuffer = useCallback((next: string) => {
    bufferRef.current = next;
    setBufferState(next);
  }, []);

  const resync = useCallback(async () => {
    const sid = sessionId.current;
    if (sid === null) return;
    try {
      const snap = await getSession(sid);
      version.current = snap.version;
      serverBuffer.current = snap.buffer;
      setBuffer(snap.buffer);
    } catch {
      // stream/heartbeat will surface a persistent failure; ignore transient
    }
  }, [setBuffer]);

  // Drainable op sender: while the local buffer is ahead of the server, send
  // the diff and await the ack, looping until caught up. Only one pump runs at
  // a time; `pump()` returns the in-flight run's promise, so `save` can await it
  // to guarantee every keystroke reached the server before checkpointing.
  const pump = useCallback((): Promise<void> => {
    if (pumpPromise.current) return pumpPromise.current;
    if (sessionId.current === null) return Promise.resolve();
    const run = (async () => {
      while (sessionId.current !== null) {
        const change = diffToChange(serverBuffer.current, bufferRef.current);
        if (change === null) return; // caught up
        const sent = bufferRef.current;
        try {
          const { version: v } = await sendOp(
            sessionId.current,
            version.current,
            [change],
          );
          version.current = v;
          serverBuffer.current = sent;
        } catch (err) {
          if (err instanceof ApiError && err.status === 409) await resync();
          return; // stop this drain; a resync (or transient failure) handled it
        }
      }
    })();
    // Set the in-flight handle *before* attaching the completion hook. A drain
    // that finds no change resolves synchronously; clearing the handle from an
    // inline `finally` would run before this assignment and leave a resolved
    // promise stuck in `pumpPromise` — poisoning every future pump (ops would
    // silently stop sending). The `=== run` guard clears only our own run.
    pumpPromise.current = run;
    void run.finally(() => {
      if (pumpPromise.current === run) pumpPromise.current = null;
    });
    return run;
  }, [resync]);

  const scheduleFlush = useCallback(() => {
    if (flushTimer.current) clearTimeout(flushTimer.current);
    flushTimer.current = setTimeout(() => void pump(), FLUSH_DELAY_MS);
  }, [pump]);

  const onChange = useCallback(
    (next: string) => {
      setBuffer(next);
      scheduleFlush();
    },
    [setBuffer, scheduleFlush],
  );

  const onFrame = useCallback(
    (frame: CoeditFrame) => {
      if (frame.type === "presence") {
        setParticipants(frame.participants);
        return;
      }
      if (frame.type === "resync") {
        void resync();
        return;
      }
      if (frame.type === "op") {
        // Skip our own echo — but only when we actually have an id, else a
        // null-authored server op would be dropped when myUserId is unresolved.
        if (myUserId !== null && frame.author === myUserId) return;
        // Only splice when we're fully caught up (no unsent local edits and no
        // op draining); otherwise re-fetch to avoid applying at stale offsets.
        if (
          pumpPromise.current !== null ||
          bufferRef.current !== serverBuffer.current
        ) {
          void resync();
          return;
        }
        let next = serverBuffer.current;
        for (const c of frame.changes) next = applyChange(next, c);
        version.current = frame.version;
        serverBuffer.current = next;
        setBuffer(next);
      }
      // cursor frames are ignored until the CodeMirror editor renders carets
    },
    [myUserId, resync, setBuffer],
  );

  const stop = useCallback(() => {
    if (flushTimer.current) {
      clearTimeout(flushTimer.current);
      flushTimer.current = null;
    }
    abort.current?.abort();
    abort.current = null;
    const sid = sessionId.current;
    sessionId.current = null;
    setActive(false);
    if (sid !== null) void leaveSession(sid).catch(() => {});
  }, []);

  const save = useCallback(async () => {
    // A Save clicked during the join round-trip must not no-op: wait for the
    // join to resolve so the session exists (and pre-join edits are flushed).
    if (sessionId.current === null && joinPromise.current) {
      await joinPromise.current;
    }
    const sid = sessionId.current;
    if (sid === null) return;
    // Drain pending edits before committing so a keystroke typed inside the
    // debounce window still reaches git. Cancel the timer, then await the pump
    // (kicking one for the tail diff) so the server has the full buffer.
    if (flushTimer.current) {
      clearTimeout(flushTimer.current);
      flushTimer.current = null;
    }
    await pump();
    try {
      await checkpointSession(sid);
    } finally {
      stop();
      onEnd?.();
    }
  }, [pump, stop, onEnd]);

  const discard = useCallback(async () => {
    const sid = sessionId.current;
    if (sid !== null) {
      // Reset the shared buffer to the committed body so the leave-time
      // checkpoint is a no-op (nothing of this edit lands in git).
      const change = diffToChange(serverBuffer.current, committedBody);
      if (change !== null) {
        try {
          await sendOp(sid, version.current, [change]);
        } catch {
          // best-effort; if it races, the checkpoint merge reconciles
        }
      }
    }
    stop();
    onEnd?.();
  }, [committedBody, stop, onEnd]);

  // Join + stream while enabled; leave on disable/unmount.
  useEffect(() => {
    if (!enabled) return;
    const ctrl = new AbortController();
    abort.current = ctrl;
    let cancelled = false;

    // Seed the buffer with the committed body immediately so the textarea shows
    // content (and callers' `buffer !== committedBody` dirty check is accurate)
    // during the join round-trip, rather than a blank flash.
    const seed = committedBody;
    serverBuffer.current = seed;
    setBuffer(seed);

    const run = (async () => {
      try {
        const snap = await joinSession(path);
        if (cancelled) return;
        sessionId.current = snap.session_id;
        version.current = snap.version;
        serverBuffer.current = snap.buffer;
        // Adopt the server buffer unless the user already typed into the seed —
        // don't clobber edits made during the join; pump() sends their diff.
        if (bufferRef.current === seed) setBuffer(snap.buffer);
        setParticipants(snap.participants);
        setActive(true);
        // Flush anything typed during the join window before streaming.
        void pump();
        // Long-lived stream; resolves when the server closes or we abort.
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

  return { active, buffer, participants, onChange, save, discard };
}
