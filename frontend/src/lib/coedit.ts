/** Client for the co-editing session API (`/api/coedit/*`).
 *
 * A page is edited by joining a live session: the shared buffer lives on the
 * server, edits are sent as range-change ops, and inbound ops/presence/resync
 * arrive over an SSE stream. Save (checkpoint) commits the buffer to git; save
 * or disconnect leaves the session. See the backend in `app/api/coedit.py` and
 * the design in `design/Co-Editing.md`.
 *
 * Offsets are UTF-16 code units — which is exactly what JS string indexing and
 * `slice` use, so `diffToChange`/`applyChange` interoperate with the server
 * (`Change` in `app/wiki/coedit.py`) without conversion.
 */
import { apiFetch, apiStream } from "./api";

/** One range-replacement edit: replace `[from, to)` (UTF-16) with `insert`. */
export interface CoeditChange {
  from: number;
  to: number;
  insert: string;
}

export interface CoeditParticipant {
  user_id: string;
  user_display: string;
  joined_at: string;
  last_seen_at: string;
}

/** A peer's live caret/selection, from their latest `cursor` frame. Offsets are
 * UTF-16 code units (JS-native), collapsed (anchor === head) = caret. */
export interface CoeditPeer {
  user_id: string;
  user_display: string;
  anchor: number;
  head: number;
}

/** Snapshot returned by join / session (the live buffer + roster). */
export interface CoeditSession {
  session_id: number;
  buffer: string;
  version: number;
  base_sha: string | null;
  participants: CoeditParticipant[];
}

/** Frames pushed over the SSE stream (`coedit_channel.py`). */
export type CoeditFrame =
  | { type: "presence"; session_id: number; participants: CoeditParticipant[] }
  | {
      type: "op";
      session_id: number;
      version: number;
      changes: CoeditChange[];
      author: string | null;
      client_id: string | null;
    }
  | {
      type: "cursor";
      session_id: number;
      user_id: string;
      user_display: string;
      anchor: number;
      head: number;
      typing: boolean;
    }
  | { type: "resync"; session_id: number; version: number };

export function joinSession(path: string): Promise<CoeditSession> {
  return apiFetch("/coedit/join", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export function getSession(sessionId: number): Promise<CoeditSession> {
  return apiFetch(`/coedit/session?session_id=${sessionId}`);
}

export function leaveSession(sessionId: number): Promise<void> {
  return apiFetch("/coedit/leave", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId }),
  });
}

/** Apply an edit op. Resolves to the new version, or throws `ApiError` with
 * status 409 when `baseVersion` is stale (caller rebases the missed ops from
 * `getOps` and retries). `clientId` tags the op so the sender can recognize its
 * own echo. */
export function sendOp(
  sessionId: number,
  baseVersion: number,
  changes: CoeditChange[],
  clientId?: string,
): Promise<{ version: number }> {
  return apiFetch("/coedit/op", {
    method: "POST",
    body: JSON.stringify({
      session_id: sessionId,
      base_version: baseVersion,
      changes,
      ...(clientId ? { client_id: clientId } : {}),
    }),
  });
}

export function sendCursor(
  sessionId: number,
  anchor: number,
  head: number,
  typing: boolean,
): Promise<void> {
  return apiFetch("/coedit/cursor", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, anchor, head, typing }),
  });
}

export function checkpointSession(
  sessionId: number,
): Promise<{ queued: boolean }> {
  return apiFetch("/coedit/checkpoint", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId }),
  });
}

/** One logged operation (one version bump) — mirrors the SSE `op` frame. */
export interface CoeditOperation {
  version: number;
  author: string;
  client_id: string | null;
  changes: CoeditChange[];
}

/** Ops after `since_version` (oldest first) + the current head version. Used to
 * rebase un-acked local edits after a stale op / gap (`GET /coedit/ops`). */
export interface CoeditOps {
  session_id: number;
  current_head_version: number;
  ops: CoeditOperation[];
}

export function getOps(
  sessionId: number,
  sinceVersion: number,
): Promise<CoeditOps> {
  return apiFetch(
    `/coedit/ops?session_id=${sessionId}&since_version=${sinceVersion}`,
  );
}

/** Open the SSE stream; `onFrame` fires per frame until `signal` aborts (or the
 * server closes). Opening the stream also joins the caller as a participant. */
export function streamSession(
  sessionId: number,
  onFrame: (frame: CoeditFrame) => void,
  signal: AbortSignal,
): Promise<void> {
  return apiStream(
    `/coedit/stream?session_id=${sessionId}`,
    { method: "GET" },
    (data) => onFrame(data as CoeditFrame),
    signal,
  );
}

/** Diff `oldStr` → `newStr` into one range change (trim common prefix/suffix),
 * or null if unchanged. Offsets are UTF-16 code units (JS-native), matching the
 * server. Coarse (one span), which is all the server needs. */
export function diffToChange(
  oldStr: string,
  newStr: string,
): CoeditChange | null {
  if (oldStr === newStr) return null;
  const oldLen = oldStr.length;
  const newLen = newStr.length;
  const maxPre = Math.min(oldLen, newLen);
  let pre = 0;
  while (pre < maxPre && oldStr.charCodeAt(pre) === newStr.charCodeAt(pre))
    pre++;
  const maxSuf = Math.min(oldLen, newLen) - pre;
  let suf = 0;
  while (
    suf < maxSuf &&
    oldStr.charCodeAt(oldLen - 1 - suf) === newStr.charCodeAt(newLen - 1 - suf)
  ) {
    suf++;
  }
  return {
    from: pre,
    to: oldLen - suf,
    insert: newStr.slice(pre, newLen - suf),
  };
}

/** Apply a range change to a string (UTF-16 offsets). */
export function applyChange(str: string, c: CoeditChange): string {
  return str.slice(0, c.from) + c.insert + str.slice(c.to);
}
