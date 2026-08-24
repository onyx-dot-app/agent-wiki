/** Client for the live-session endpoint (`/api/coedit/ws`) — one WebSocket
 * per session, multiplexing raw binary Yjs sync/awareness frames (the
 * document + presence themselves) alongside a small set of JSON control
 * messages (join handshake, presence roster, explicit checkpoint).
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
import { opaqueId } from "@/lib/editor/ids";
import {
  encodeAwarenessMessage,
  encodeSyncStep1,
  encodeUpdateMessage,
  handleMessage,
} from "@/lib/editor/yProtocol";

/** How long to wait for a `checkpoint_result` before giving up on that save.
 *
 * A checkpoint is normally tens to low-hundreds of milliseconds, so this only
 * ever fires on something genuinely wrong. It is sized off the two legitimately
 * slow paths so it can't fire on a save that is merely working hard: the
 * server's `_CHECKPOINT_LOCK_TIMEOUT_MS` (30s — how long a duplicate checkpoint
 * waits for the one in progress), plus room for the AI merge that resolves an
 * overlap with an agent commit, which the backend measures in seconds. Move the
 * two together if either changes.
 *
 * Without a deadline the promise settles only on the ack or on `onclose`, so an
 * ack that is published but never delivered left it pending forever — and since
 * `hooks.ts` holds `saveInFlight` across the await, that silently stopped the
 * tab autosaving for the rest of the connection while showing "Saving…".
 */
const CHECKPOINT_TIMEOUT_MS = 60_000;

/** Marks the timeout rejection so `hooks.ts` can budget its retries
 * separately: this failure costs a full minute, unlike "not connected", which
 * fails instantly and is what the 10-attempt budget was written for. */
export const CHECKPOINT_TIMED_OUT = "checkpoint timed out";

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
  /** The session's CRDT lineage generation at join, or null when the server
   * predates the field (mid rolling deploy). Changes only when the server
   * reseeds the document (a checkpoint divergence it couldn't splice); a doc
   * built against a different generation can never converge with the session
   * again and must be discarded, not synced. */
  lineage: number | null;
  participants: CoeditParticipant[];
}

/** A joined session's snapshot plus `closed` — a promise that resolves once
 * the connection ends, for any reason. `connectSession`'s own promise
 * settles at *join*, not at connection-end; `closed` is the separate handle
 * the reconnect loop awaits for the connection's whole lifetime. */
export interface CoeditConnection extends CoeditSession {
  /** True when this join landed on a different lineage generation than the
   * caller's doc was built against (`expectedLineage`). The connection has
   * already suppressed every sync exchange; the caller must close it,
   * rebuild the doc, and rejoin. */
  staleLineage: boolean;
  closed: Promise<{ code: number; expected: boolean; resync: boolean }>;
  /** Handle for `closeSession`/`checkpointSession` — identifies this socket,
   * not the session, so a reconnect can't be mistaken for it. */
  connectionId: number;
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

// connectionId -> its live WebSocket + in-flight checkpoint correlation. One
// entry per `connectSession` call, removed when that call's own socket closes.
//
// Keyed on the connection, NOT the session: a session outlives any single
// socket (the server keeps one active session per page), so a reconnect joins
// the *same* session_id while the previous socket may still be closing. Keyed
// by session id, the replacement overwrote the old entry, the old socket's
// onclose then deleted the replacement's, and `closeSession`/
// `checkpointSession` resolved to whichever socket happened to be registered —
// so a teardown could close a freshly-established connection, and mark it an
// *expected* close, which tells the reconnect loop to give up. Silently dead
// editor. A per-connection key removes the ambiguity instead of racing it.
const sockets = new Map<number, TrackedSocket>();
let nextConnectionId = 1;

/** Open the live session for `path`, binding `doc`/`awareness` to it for
 * the connection's lifetime: local changes to either are sent out as
 * binary frames, inbound binary frames are applied to them directly. The
 * WS connection's `joined` frame *is* the join response.
 *
 * `onPresence` fires with the full roster on every membership change.
 *
 * A checkpoint's merge or an out-of-band commit folded into the session
 * normally arrives as an ordinary Yjs update on this same connection, which
 * Yjs integrates against whatever this client has — the caret stays put and
 * pending local edits rebase over it. The one exception is `resync_required`:
 * the server *replaced* the document's CRDT lineage (a divergence it couldn't
 * splice), so this doc can never converge again — the connection closes with
 * `resync: true` and the caller must rebuild the doc and rejoin.
 */
export function connectSession(
  path: string,
  doc: Y.Doc,
  awareness: Awareness,
  // The lineage generation `doc` last synced against, or null for a fresh
  // doc. If the `joined` frame reports a different generation, every sync
  // exchange on this connection is suppressed immediately: the server's
  // sync request arrives right behind `joined`, and answering it would push
  // this replaced-lineage doc's content onto the reseeded session — on a
  // connection the server has no reason to distrust. The caller still gets
  // the resolved connection and does the visible handling (close + rebuild).
  expectedLineage: number | null,
  onPresence: (participants: CoeditParticipant[]) => void,
): Promise<CoeditConnection> {
  return new Promise((resolve, reject) => {
    const connectionId = nextConnectionId++;
    const ws = new WebSocket(
      apiSocketUrl(`/coedit/ws?path=${encodeURIComponent(path)}`),
    );
    ws.binaryType = "arraybuffer";
    const pendingCheckpoints = new Map<string, PendingCheckpoint>();
    let joined = false;
    // Set by a `join_error` frame — onclose (right behind it) must not
    // overwrite that specific rejection with its own generic message.
    let failedWithDetail = false;
    let expectedClose = false;
    // Set by a `resync_required` frame: the server replaced the document's
    // CRDT lineage (a checkpoint reseed), so this doc must be discarded and
    // the session rejoined fresh — syncing it further would union old and
    // new content. Carried out through `closed` for the reconnect loop.
    let resyncRequested = false;
    let sessionId: number | null = null;
    let resolveClosed:
      | ((info: { code: number; expected: boolean; resync: boolean }) => void)
      | null = null;
    const closed = new Promise<{
      code: number;
      expected: boolean;
      resync: boolean;
    }>((res) => {
      resolveClosed = res;
    });

    // Local Yjs changes -> outbound binary frames. `origin === "remote"`
    // filters out re-broadcasting what handleMessage just applied *from*
    // the server — only genuine local edits/presence changes go back out.
    // Nothing outbound before `joined` (and its lineage verdict), and nothing
    // after the verdict goes stale: an edit typed in the open->joined window
    // would otherwise be sent from a doc whose lineage the server hasn't
    // vouched for yet — on a reconnect after a reseed, that's
    // replaced-lineage content entering the log through a connection the
    // server's own guard has no reason to distrust. Nothing is lost by
    // waiting: the post-join sync exchange carries any local state the
    // server is missing.
    const onDocUpdate = (update: Uint8Array, origin: unknown) => {
      if (!joined || resyncRequested) return;
      if (origin === "remote" || ws.readyState !== WebSocket.OPEN) return;
      ws.send(encodeUpdateMessage(update));
    };
    const onAwarenessUpdate = (
      changes: { added: number[]; updated: number[]; removed: number[] },
      origin: unknown,
    ) => {
      if (!joined || resyncRequested) return;
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
        // Once a resync is known to be needed, this doc must neither absorb
        // the session's frames nor answer the server's sync request — its
        // reply would carry replaced-lineage content.
        if (resyncRequested) return;
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
        case "join_error": {
          // Unlike a generic pre-accept denial (which a real browser can
          // only ever see as a bare close, code 1006 — see onclose below),
          // this is a real post-accept frame: the server sends it, then
          // closes right after. Reject with the server's actual detail so
          // the caller can tell "retrying is pointless" (a codec-
          // unsupported page) apart from "transient, worth retrying."
          failedWithDetail = true;
          reject(new ApiError(422, msg.detail as string));
          return;
        }
        case "joined": {
          joined = true;
          sessionId = msg.session_id as number;
          // Absent/malformed lineage (a backend without the field, mid
          // rolling deploy) is null — never compared, so a mixed-version
          // window can't fire a spurious resync that discards local edits.
          const lineage = typeof msg.lineage === "number" ? msg.lineage : null;
          // Mismatched generation: suppress sync from this instant (see
          // `expectedLineage`); the caller closes and rebuilds on seeing
          // `staleLineage` on the resolved connection.
          const staleLineage =
            expectedLineage !== null &&
            lineage !== null &&
            lineage !== expectedLineage;
          if (staleLineage) resyncRequested = true;
          sockets.set(connectionId, {
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
            lineage,
            staleLineage,
            participants: msg.participants as CoeditParticipant[],
            closed,
            connectionId,
          });
          return;
        }
        case "resync_required": {
          // The server reseeded the session's document; our doc is a replaced
          // lineage and every further sync frame would only union the two.
          // Close and let the reconnect loop rebuild the doc and rejoin.
          resyncRequested = true;
          ws.close();
          return;
        }
        case "ping":
          return;
        case "presence":
          onPresence(msg.participants as CoeditParticipant[]);
          return;
        case "checkpoint_result": {
          const p = pendingCheckpoints.get(msg.request_id as string);
          if (!p) return;
          pendingCheckpoints.delete(msg.request_id as string);
          if (msg.ok) p.resolve();
          // The frame carries a reason ("forbidden" for a viewer's save, or
          // the failure the task hit) — surface it instead of a generic
          // message the user can't act on.
          else
            p.reject(
              new ApiError(
                msg.error === "forbidden" ? 403 : 500,
                typeof msg.error === "string" && msg.error
                  ? `Could not save: ${msg.error}`
                  : "Could not save this page.",
              ),
            );
          return;
        }
        default:
          return;
      }
    };

    ws.onclose = (event) => {
      unbind();
      sockets.delete(connectionId);
      for (const p of pendingCheckpoints.values()) {
        p.reject(new ApiError(0, "connection closed"));
      }
      pendingCheckpoints.clear();
      if (!joined) {
        // A `join_error` frame already rejected with the server's actual,
        // distinguishable reason — don't clobber it with the generic
        // message below (this close is expected to follow right behind
        // it).
        if (failedWithDetail) return;
        // The join handshake itself failed — surface it rather than retry
        // silently (there's no read-only fallback to fall back to). No
        // permission-specific message here: a pre-accept HTTP-level denial
        // always reports as the browser's generic close code 1006 — same
        // reasoning as the OT-era client this replaces.
        reject(new ApiError(0, "Failed to join the editing session."));
        return;
      }
      resolveClosed?.({
        code: event.code,
        expected: expectedClose,
        resync: resyncRequested,
      });
    };

    // A no-op: onclose (which always follows an error per the WebSocket
    // spec) does the real handling.
    ws.onerror = () => {};
  });
}

/** Close one connection, by its `connectionId`. A no-op if already closed or
 * never joined. Resolves that connection's `closed` with `expected: true`, so
 * its own reconnect loop knows not to reconnect — which is why this must never
 * be able to land on a different, live socket. */
export function closeSession(connectionId: number): void {
  const entry = sockets.get(connectionId);
  if (!entry) return;
  entry.markExpectedClose();
  entry.ws.close();
}

/** Commit the live doc to git, over one specific connection. Resolves once the
 * server has committed (or determined there was nothing to commit). */
export function checkpointSession(connectionId: number): Promise<void> {
  const entry = sockets.get(connectionId);
  if (!entry || entry.ws.readyState !== WebSocket.OPEN) {
    return new Promise<void>((_resolve, reject) => {
      setTimeout(() => reject(new ApiError(0, "not connected")), 0);
    });
  }
  const requestId = opaqueId();
  return new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      // Drop the correlation first: a late ack must not settle a promise this
      // has already rejected, and leaving the entry would leak one per save.
      entry.pendingCheckpoints.delete(requestId);
      reject(new ApiError(0, CHECKPOINT_TIMED_OUT));
    }, CHECKPOINT_TIMEOUT_MS);
    entry.pendingCheckpoints.set(requestId, {
      resolve: () => {
        clearTimeout(timer);
        resolve();
      },
      reject: (err) => {
        clearTimeout(timer);
        reject(err);
      },
    });
    entry.ws.send(
      JSON.stringify({ type: "checkpoint", request_id: requestId }),
    );
  });
}
