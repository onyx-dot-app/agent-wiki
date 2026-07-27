/** Client-side wire framing for the co-edit WebSocket's binary Yjs
 * sync/awareness protocol — see `app/api/coedit.py`'s module docstring for
 * the server side, and `app/wiki/coedit_room.py` for why the document
 * itself can only ever live in one process's memory.
 *
 * Outer envelope: `[messageType, ...innerBytes]` where `messageType` is
 * `0` (sync) or `1` (awareness) — the same convention `pycrdt`'s
 * `YMessageType` uses on the backend and the standard y-websocket
 * ecosystem convention `y-protocols/sync` itself documents. Confirmed
 * byte-compatible against the real backend directly (constructing a
 * message here, decoding it with `pycrdt.handle_sync_message` in Python,
 * and back), not assumed from the docs alone.
 */
import * as decoding from "lib0/decoding";
import * as encoding from "lib0/encoding";
import {
  Awareness,
  applyAwarenessUpdate,
  encodeAwarenessUpdate,
} from "y-protocols/awareness";
import * as syncProtocol from "y-protocols/sync";
import type * as Y from "yjs";

const MESSAGE_SYNC = 0;
const MESSAGE_AWARENESS = 1;

/** The connection's opening move: offer our state vector so the server can
 * send back exactly what we're missing. */
export function encodeSyncStep1(doc: Y.Doc): Uint8Array {
  const encoder = encoding.createEncoder();
  encoding.writeVarUint(encoder, MESSAGE_SYNC);
  syncProtocol.writeSyncStep1(encoder, doc);
  return encoding.toUint8Array(encoder);
}

/** A local content change, ready to broadcast. */
export function encodeUpdateMessage(update: Uint8Array): Uint8Array {
  const encoder = encoding.createEncoder();
  encoding.writeVarUint(encoder, MESSAGE_SYNC);
  syncProtocol.writeUpdate(encoder, update);
  return encoding.toUint8Array(encoder);
}

/** A local awareness (cursor/presence) change, ready to broadcast. */
export function encodeAwarenessMessage(
  awareness: Awareness,
  changedClients: number[],
): Uint8Array {
  const encoder = encoding.createEncoder();
  encoding.writeVarUint(encoder, MESSAGE_AWARENESS);
  encoding.writeVarUint8Array(
    encoder,
    encodeAwarenessUpdate(awareness, changedClients),
  );
  return encoding.toUint8Array(encoder);
}

/** Handle one inbound binary frame. Applies it to `doc`/`awareness` as a
 * side effect; returns a reply to send back (a SYNC_STEP2, only ever in
 * response to a SYNC_STEP1), or `null` when there's nothing to reply with. */
export function handleMessage(
  data: Uint8Array,
  doc: Y.Doc,
  awareness: Awareness,
): Uint8Array | null {
  const decoder = decoding.createDecoder(data);
  const messageType = decoding.readVarUint(decoder);
  if (messageType === MESSAGE_SYNC) {
    const replyEncoder = encoding.createEncoder();
    encoding.writeVarUint(replyEncoder, MESSAGE_SYNC);
    // Only writes a reply into replyEncoder for a SYNC_STEP1 input (replies
    // with SYNC_STEP2); a STEP2/UPDATE input applies directly and leaves it
    // untouched beyond the messageType byte just written above.
    syncProtocol.readSyncMessage(decoder, replyEncoder, doc, "remote");
    return encoding.length(replyEncoder) > 1
      ? encoding.toUint8Array(replyEncoder)
      : null;
  }
  if (messageType === MESSAGE_AWARENESS) {
    applyAwarenessUpdate(
      awareness,
      decoding.readVarUint8Array(decoder),
      "remote",
    );
    return null;
  }
  return null;
}
