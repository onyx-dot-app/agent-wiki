/** API client for the co-editing session endpoints (`/api/coedit/*`).
 *
 * A page is edited by joining a live session: the shared buffer lives on the
 * server, edits are sent as range-change ops, and inbound ops/presence/resync
 * arrive over an SSE stream. Save (checkpoint) commits the buffer to git; save
 * or disconnect leaves the session. See the backend in `app/api/coedit.py` and
 * the design in `design/Co-Editing.md`.
 *
 * Offsets are UTF-16 code units — which is exactly what JS string indexing and
 * `slice` use, so the ops interoperate with the server
 * (`Change` in `app/wiki/coedit.py`) without conversion; see `utils.ts` for
 * `diffToChange`/`applyChange`.
 */
import { apiFetch, apiStream } from "@/lib/api";
import type {
  CoeditChange,
  CoeditFrame,
  CoeditOps,
  CoeditSession,
} from "@/lib/coeditor/types";

/** Join (or re-join) the live session for `path`, returning the initial buffer + roster. */
export function joinSession(path: string): Promise<CoeditSession> {
  return apiFetch("/coedit/join", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

/** Fetch the current session snapshot (buffer + roster) without joining. */
export function getSession(sessionId: number): Promise<CoeditSession> {
  return apiFetch(`/coedit/session?session_id=${sessionId}`);
}

/** Leave the session, allowing the server to clean up presence + buffer if empty. */
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

/** Report the local caret/selection to the server so peers see live presence. */
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

/** Commit the session buffer to git. Returns `queued: true` when the write was
 * handed off to the background worker rather than applied inline. */
export function checkpointSession(
  sessionId: number,
): Promise<{ queued: boolean }> {
  return apiFetch("/coedit/checkpoint", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId }),
  });
}

/** Fetch all ops after `sinceVersion` (oldest first) plus the current head
 * version. Used to rebase un-acked local edits after a 409 or a version gap. */
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
