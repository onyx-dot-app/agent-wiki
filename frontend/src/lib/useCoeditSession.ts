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
  const inFlight = useRef(false);
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abort = useRef<AbortController | null>(null);
  const endedRef = useRef(false);

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
      inFlight.current = false;
      setBuffer(snap.buffer);
    } catch {
      // stream/heartbeat will surface a persistent failure; ignore transient
    }
  }, [setBuffer]);

  // Send the pending diff (serverBuffer → bufferRef) if idle; coalesces via a
  // single in-flight op and re-flushes when the ack lands.
  const flush = useCallback(() => {
    const sid = sessionId.current;
    if (sid === null || inFlight.current) return;
    const change = diffToChange(serverBuffer.current, bufferRef.current);
    if (change === null) return;
    const sent = bufferRef.current;
    inFlight.current = true;
    sendOp(sid, version.current, [change])
      .then(({ version: v }) => {
        version.current = v;
        serverBuffer.current = sent;
        inFlight.current = false;
        if (bufferRef.current !== serverBuffer.current) flush();
      })
      .catch((err) => {
        inFlight.current = false;
        if (err instanceof ApiError && err.status === 409) void resync();
      });
  }, [resync]);

  const scheduleFlush = useCallback(() => {
    if (flushTimer.current) clearTimeout(flushTimer.current);
    flushTimer.current = setTimeout(flush, FLUSH_DELAY_MS);
  }, [flush]);

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
        if (frame.author === myUserId) return; // our own echo, already applied
        // Only splice when we're fully caught up (no unsent local edits and no
        // op in flight); otherwise re-fetch to avoid applying at stale offsets.
        if (inFlight.current || bufferRef.current !== serverBuffer.current) {
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
    const sid = sessionId.current;
    if (sid === null) return;
    // Flush any tail edit synchronously-ish before committing, then checkpoint.
    if (flushTimer.current) {
      clearTimeout(flushTimer.current);
      flushTimer.current = null;
    }
    try {
      await checkpointSession(sid);
    } finally {
      stop();
      onEnd?.();
    }
  }, [stop, onEnd]);

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
    endedRef.current = false;
    const ctrl = new AbortController();
    abort.current = ctrl;
    let cancelled = false;

    (async () => {
      try {
        const snap = await joinSession(path);
        if (cancelled) return;
        sessionId.current = snap.session_id;
        version.current = snap.version;
        serverBuffer.current = snap.buffer;
        setBuffer(snap.buffer);
        setParticipants(snap.participants);
        setActive(true);
        // Long-lived stream; resolves when the server closes or we abort.
        await streamSession(snap.session_id, onFrame, ctrl.signal);
      } catch {
        // join failed or stream ended; leave edit mode gracefully
      }
    })();

    return () => {
      cancelled = true;
      stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, path]);

  return { active, buffer, participants, onChange, save, discard };
}
