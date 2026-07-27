/** Client for the live-session endpoint (`/api/coedit/ws`) — one WebSocket
 * per session, multiplexing raw binary Yjs sync/awareness frames (the
 * document + presence themselves) alongside a small set of JSON control
 * messages (join handshake, presence roster, resync, explicit checkpoint).
 * See `app/api/coedit.py`'s module docstring for the server side and
 * `yProtocol.ts` for the binary framing, confirmed byte-compatible against
 * the real backend directly.
 *
 * There's no explicit "leave" call: the server treats any socket close
 * (explicit, network drop, or a killed tab) as the leave signal, which
 * doesn't depend on the client successfully transmitting anything during
 * teardown. `closeSession` is that close — it just closes the connection.
 */
import type { Awareness } from "y-protocols/awareness";
import type * as Y from "yjs";
import { apiSocketUrl, ApiError } from "@/lib/api";
import {
  encodeAwarenessMessage,
  encodeSyncStep1,
  encodeUpdateMessage,
  handleMessage,
} from "@/lib/editor/yProtocol";

export interface CoeditParticipant {
  user_id: string;
  user_display: string;
  joined_at: string;
  last_seen_at: string;
  last_edited_at: string | null;
}

export interface CoeditSession {
  session_id: number;
  base_sha: string | null;
  can_write: boolean;
  participants: CoeditParticipant[];
}

/** A joined session's snapshot plus `closed` — a promise that resolves once
 * the connection ends, for any reason. `connectSession`'s own promise
 * settles at *join*, not at connection-end; `closed` is the separate handle
 * the reconnect loop awaits for the connection's whole lifetime. */
export interface CoeditConnection extends CoeditSession {
  closed: Promise<{ code: number; expected: boolean }>;
}

interface PendingCheckpoint {
  resolve: () => void;
  reject: (err: Error) => void;
}

interface TrackedSocket {
  ws: WebSocket;
  pendingCheckpoints: Map<string, PendingCheckpoint>;
  markExpectedClose: () => void;
}

// sessionId -> its live WebSocket + in-flight checkpoint correlation. One
// entry per `connectSession` call, removed on close.
const sockets = new Map<number, TrackedSocket>();

/** Open the live session for `path`, binding `doc`/`awareness` to it for
 * the connection's lifetime: local changes to either are sent out as
 * binary frames, inbound binary frames are applied to them directly. The
 * WS connection's `joined` frame *is* the join response.
 *
 * `onPresence` fires with the full roster on every membership change;
 * `onResync` fires when the server replaced the doc wholesale (a live
 * checkpoint's merge or a live-rebase folded in an out-of-band commit —
 * see `app/models/coedit.py`'s `ResyncFrame`) and the caller must reconnect
 * rather than keep trusting this connection's incremental state.
 */
export function connectSession(
  path: string,
  doc: Y.Doc,
  awareness: Awareness,
  onPresence: (participants: CoeditParticipant[]) => void,
  onResync: () => void,
): Promise<CoeditConnection> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(
      apiSocketUrl(`/coedit/ws?path=${encodeURIComponent(path)}`),
    );
    ws.binaryType = "arraybuffer";
    const pendingCheckpoints = new Map<string, PendingCheckpoint>();
    let joined = false;
    let expectedClose = false;
    let sessionId: number | null = null;
    let resolveClosed:
      | ((info: { code: number; expected: boolean }) => void)
      | null = null;
    const closed = new Promise<{ code: number; expected: boolean }>((res) => {
      resolveClosed = res;
    });

    // Local Yjs changes -> outbound binary frames. `origin === "remote"`
    // filters out re-broadcasting what handleMessage just applied *from*
    // the server — only genuine local edits/presence changes go back out.
    const onDocUpdate = (update: Uint8Array, origin: unknown) => {
      if (origin === "remote" || ws.readyState !== WebSocket.OPEN) return;
      ws.send(encodeUpdateMessage(update));
    };
    const onAwarenessUpdate = (
      changes: { added: number[]; updated: number[]; removed: number[] },
      origin: unknown,
    ) => {
      if (origin === "remote" || ws.readyState !== WebSocket.OPEN) return;
      const changed = changes.added.concat(changes.updated, changes.removed);
      if (changed.length === 0) return;
      ws.send(encodeAwarenessMessage(awareness, changed));
    };
    doc.on("update", onDocUpdate);
    awareness.on("update", onAwarenessUpdate);
    const unbind = () => {
      doc.off("update", onDocUpdate);
      awareness.off("update", onAwarenessUpdate);
    };

    ws.onopen = () => {
      // Offer our state (empty, for a fresh connection) so the server can
      // send back exactly what we're missing — the standard two-way sync
      // handshake; independent of the server's own opening frames.
      ws.send(encodeSyncStep1(doc));
    };

    ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        const reply = handleMessage(new Uint8Array(event.data), doc, awareness);
        if (reply && ws.readyState === WebSocket.OPEN) ws.send(reply);
        return;
      }
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(event.data as string);
      } catch {
        return;
      }
      switch (msg.type) {
        case "joined": {
          joined = true;
          sessionId = msg.session_id as number;
          sockets.set(sessionId, {
            ws,
            pendingCheckpoints,
            markExpectedClose: () => {
              expectedClose = true;
            },
          });
          resolve({
            session_id: sessionId,
            base_sha: msg.base_sha as string | null,
            can_write: msg.can_write as boolean,
            participants: msg.participants as CoeditParticipant[],
            closed,
          });
          return;
        }
        case "ping":
          return;
        case "presence":
          onPresence(msg.participants as CoeditParticipant[]);
          return;
        case "resync":
          onResync();
          return;
        case "checkpoint_result": {
          const p = pendingCheckpoints.get(msg.request_id as string);
          if (!p) return;
          pendingCheckpoints.delete(msg.request_id as string);
          if (msg.ok) p.resolve();
          else p.reject(new ApiError(500, "checkpoint failed"));
          return;
        }
        default:
          return;
      }
    };

    ws.onclose = (event) => {
      unbind();
      if (sessionId !== null) sockets.delete(sessionId);
      for (const p of pendingCheckpoints.values()) {
        p.reject(new ApiError(0, "connection closed"));
      }
      pendingCheckpoints.clear();
      if (!joined) {
        // The join handshake itself failed — surface it rather than retry
        // silently (there's no read-only fallback to fall back to). No
        // permission-specific message here: a pre-accept HTTP-level denial
        // always reports as the browser's generic close code 1006 — same
        // reasoning as the OT-era client this replaces.
        reject(new ApiError(0, "Failed to join the editing session."));
        return;
      }
      resolveClosed?.({ code: event.code, expected: expectedClose });
    };

    // A no-op: onclose (which always follows an error per the WebSocket
    // spec) does the real handling.
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

/** Force this connection closed *without* marking it expected — resolves
 * `closed` with `expected: false`, so the caller's reconnect loop treats it
 * exactly like an unexpected drop and reconnects after its usual backoff.
 * For a `resync` frame (the server replaced the doc wholesale — a live
 * checkpoint's merge or a live-rebase; see `ResyncFrame`): this
 * connection's incremental Yjs state is no longer valid, so it must be
 * torn down and replaced with a fresh one, but that's not the same thing
 * as the user/hook intentionally ending the session. */
export function disconnectForResync(sessionId: number): void {
  const entry = sockets.get(sessionId);
  if (!entry) return;
  entry.ws.close();
}

/** Commit the session's live doc to git. Resolves once the server has
 * committed (or determined there was nothing to commit). */
export function checkpointSession(sessionId: number): Promise<void> {
  const entry = sockets.get(sessionId);
  if (!entry || entry.ws.readyState !== WebSocket.OPEN) {
    return new Promise<void>((_resolve, reject) => {
      setTimeout(() => reject(new ApiError(0, "not connected")), 0);
    });
  }
  const requestId = crypto.randomUUID();
  return new Promise<void>((resolve, reject) => {
    entry.pendingCheckpoints.set(requestId, { resolve, reject });
    entry.ws.send(
      JSON.stringify({ type: "checkpoint", request_id: requestId }),
    );
  });
}
