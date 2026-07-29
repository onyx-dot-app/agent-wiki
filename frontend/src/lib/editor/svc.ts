/** Client for the live-session endpoint (`/api/coedit/ws`) — one WebSocket
 * per session, multiplexing every message type (join, op, cursor,
 * checkpoint, get_ops). `sendOp`/`getOps` keep the exact signatures and
 * `Promise` shapes `components.tsx` imports directly, so that file doesn't
 * need to change; the backend domain logic/message shapes are covered in
 * `app/api/coedit.py`'s module docstring.
 *
 * There's no explicit "leave" call: the server treats any socket close
 * (explicit, network drop, or a killed tab) as the leave signal, which
 * doesn't depend on the client successfully transmitting anything during
 * teardown. `closeSession` is that close — it just closes the connection.
 *
 * Offsets are UTF-16 code units — which is exactly what JS string indexing and
 * `slice` use, so the ops interoperate with the server
 * (`Change` in `app/wiki/coedit.py`) without conversion; see `utils.ts` for
 * `diffToChange`/`applyChange`.
 */
import { apiSocketUrl, ApiError } from "@/lib/api";
import type {
  CoeditChange,
  CoeditFrame,
  CoeditOps,
  CoeditSession,
} from "@/lib/editor/types";

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
}

interface TrackedSocket {
  ws: WebSocket;
  pending: Map<string, PendingRequest>;
  markExpectedClose: () => void;
}

// sessionId -> its live WebSocket + in-flight request/response correlation.
// One entry per `connectSession` call; removed on close. Every other
// exported function looks itself up here by `sessionId`, matching the old
// client's plain-sessionId-based calls (each of which was a stateless HTTP
// request; here they all resolve to the one tracked connection).
const sockets = new Map<number, TrackedSocket>();

function newRequestId(): string {
  return crypto.randomUUID();
}

// Maps a WS `*_result` frame's `error` string onto an HTTP-style status, so
// `ApiError`-checking call sites (`components.tsx`'s `e.status === 409`
// stale-version check, specifically) can keep checking a status code.
const ERROR_STATUS: Record<string, number> = {
  stale_version: 409,
  invalid_op: 422,
  forbidden: 403,
  no_active_session: 404,
};

function errorFor(error: string | null | undefined): ApiError {
  const code = error ?? "unknown_error";
  return new ApiError(ERROR_STATUS[code] ?? 500, code);
}

function send(sessionId: number, message: Record<string, unknown>): void {
  const entry = sockets.get(sessionId);
  if (!entry || entry.ws.readyState !== WebSocket.OPEN) return;
  entry.ws.send(JSON.stringify(message));
}

function request<T>(
  sessionId: number,
  message: Record<string, unknown>,
): Promise<T> {
  const entry = sockets.get(sessionId);
  if (!entry || entry.ws.readyState !== WebSocket.OPEN) {
    // `components.tsx`'s CM6 collab layer retries a failed op unconditionally
    // and immediately, with no backoff (`doPush`'s tail call) — so this must
    // never settle synchronously. A same-tick Promise.reject() here, paired
    // with that retry, produces an unbroken chain of microtasks with no
    // yield to the browser's event loop: a real, reproduced main-thread
    // freeze, not a hypothetical. Settling on a real macrotask tick instead
    // forces a yield between retries, which is enough to keep the tab
    // responsive without touching that retry logic.
    return new Promise<T>((_resolve, reject) => {
      setTimeout(() => reject(new ApiError(0, "not connected")), 0);
    });
  }
  const requestId = newRequestId();
  return new Promise<T>((resolve, reject) => {
    entry.pending.set(requestId, {
      resolve: resolve as (value: unknown) => void,
      reject,
    });
    entry.ws.send(JSON.stringify({ ...message, request_id: requestId }));
  });
}

/** A joined session's snapshot plus `closed` — a promise that resolves once
 * the connection ends, for any reason. `connectSession`'s own promise
 * settles at *join*, not at connection-end, since the caller needs the join
 * snapshot immediately to mount the editor from; `closed` is the separate
 * handle the reconnect loop awaits for the connection's whole lifetime. */
export interface CoeditConnection extends CoeditSession {
  closed: Promise<{ code: number; expected: boolean }>;
}

/** Open the live session for `path` — the WS connection's `joined` frame
 * *is* the join response (replaces `POST /join` + separately opening
 * `GET /stream`). `onFrame` fires for every inbound broadcast frame
 * (`presence`/`op`/`cursor`/`resync`) until the connection closes. */
export function connectSession(
  path: string,
  onFrame: (frame: CoeditFrame) => void,
): Promise<CoeditConnection> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(
      apiSocketUrl(`/coedit/ws?path=${encodeURIComponent(path)}`),
    );
    const pending = new Map<string, PendingRequest>();
    let joined = false;
    let expectedClose = false;
    let sessionId: number | null = null;
    let resolveClosed:
      | ((info: { code: number; expected: boolean }) => void)
      | null = null;
    const closed = new Promise<{ code: number; expected: boolean }>((res) => {
      resolveClosed = res;
    });

    ws.onmessage = (event) => {
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(event.data as string);
      } catch {
        return;
      }
      const type = msg.type;
      if (type === "joined") {
        joined = true;
        sessionId = msg.session_id as number;
        sockets.set(sessionId, {
          ws,
          pending,
          markExpectedClose: () => {
            expectedClose = true;
          },
        });
        resolve({
          session_id: msg.session_id as number,
          buffer: msg.buffer as string,
          version: msg.version as number,
          base_sha: msg.base_sha as string | null,
          participants: msg.participants as CoeditSession["participants"],
          closed,
        });
        return;
      }
      if (type === "ping") return;
      if (
        type === "op_result" ||
        type === "checkpoint_result" ||
        type === "ops_result"
      ) {
        const p = pending.get(msg.request_id as string);
        if (!p) return;
        pending.delete(msg.request_id as string);
        if (msg.ok) {
          p.resolve(msg);
        } else {
          const error = msg.error as string | null | undefined;
          p.reject(errorFor(error));
          if (error === "no_active_session") {
            // The transport can still be healthy after the server-side
            // session has closed. Force a real socket close so the hook's
            // existing reconnect path rejoins by page path and replays its
            // locally held document onto the fresh session.
            ws.close();
          }
        }
        return;
      }
      // presence / op / cursor / resync — the broadcast frames hooks.ts handles.
      onFrame(msg as unknown as CoeditFrame);
    };

    ws.onclose = (event) => {
      if (sessionId !== null) sockets.delete(sessionId);
      for (const p of pending.values()) {
        p.reject(new ApiError(0, "connection closed"));
      }
      pending.clear();
      if (!joined) {
        // The join handshake itself failed — surface it rather than retry
        // silently (there's no read-only fallback to fall back to). No
        // permission-specific message here: a pre-accept HTTP-level denial
        // (401/403 from the backend's connect-time checks) always reports as
        // the browser's generic close code 1006 — WebSocket close codes like
        // 1008 only apply to a close the server sends *after* accepting,
        // which a rejected handshake never reaches, so there's no reliable
        // signal here to distinguish "forbidden" from any other failure.
        reject(new ApiError(0, "Failed to join the editing session."));
        return;
      }
      resolveClosed?.({ code: event.code, expected: expectedClose });
    };

    // A no-op: onclose (which always follows an error per the WebSocket
    // spec) does the real handling — this event alone would only give an
    // unhelpful generic Event with no useful failure detail.
    ws.onerror = () => {};
  });
}

/** Close the session's connection. A no-op if already closed/never
 * connected. Resolves `closed` with `expected: true`, so the caller's
 * reconnect loop knows not to reconnect. */
export function closeSession(sessionId: number): void {
  const entry = sockets.get(sessionId);
  if (!entry) return;
  entry.markExpectedClose();
  entry.ws.close();
}

/** Apply an edit op. Resolves to the new version, or throws `ApiError` with
 * status 409 when `baseVersion` is stale (caller rebases the missed ops from
 * `getOps` and retries). `clientId` tags the op so the sender can recognize its
 * own echo. */
export async function sendOp(
  sessionId: number,
  baseVersion: number,
  changes: CoeditChange[],
  clientId?: string,
  caretSeq?: number | null,
): Promise<{ version: number }> {
  const result = await request<{ version: number }>(sessionId, {
    type: "op",
    base_version: baseVersion,
    changes,
    ...(clientId ? { client_id: clientId } : {}),
    // An edit asserts caret placement at the sender's current epoch; omit
    // when the caret is cleared so a late-flushed op can't resurrect it.
    ...(caretSeq !== null && caretSeq !== undefined
      ? { caret_seq: caretSeq }
      : {}),
  });
  return { version: result.version };
}

/** Report the local caret/selection to the server so peers see live presence.
 * Fire-and-forget — a dropped ping self-heals on the next throttled send, so
 * failures are silently swallowed rather than surfaced. Null anchor/head
 * clears the caret (editor blur / hidden tab) — peers drop it and presence
 * flips the sender to "viewing". `seq` is the caret epoch that orders
 * concurrent place/clear writes server-side. */
export function sendCursor(
  sessionId: number,
  anchor: number | null,
  head: number | null,
  typing: boolean,
  seq: number,
): void {
  send(sessionId, { type: "cursor", anchor, head, typing, seq });
}

/** Commit the session buffer to git. Resolves once the server has enqueued
 * (or completed) the checkpoint. */
export async function checkpointSession(sessionId: number): Promise<void> {
  await request<{ ok: boolean }>(sessionId, { type: "checkpoint" });
}

/** Fetch all ops after `sinceVersion` (oldest first) plus the current head
 * version. Used to rebase un-acked local edits after a stale op or a gap. */
export async function getOps(
  sessionId: number,
  sinceVersion: number,
): Promise<CoeditOps> {
  const result = await request<{
    current_head_version: number;
    ops: CoeditOps["ops"];
  }>(sessionId, { type: "get_ops", since_version: sinceVersion });
  return {
    session_id: sessionId,
    current_head_version: result.current_head_version,
    ops: result.ops,
  };
}
