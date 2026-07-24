"use client";

/**
 * `useCoeditSession` — owns the Y.Doc + `y-websocket` connection lifecycle
 * for a page.
 *
 * Much smaller than the CodeMirror-era hook it replaces: presence, cursors,
 * and reconnect/backoff are handled natively by Yjs Awareness and
 * `y-websocket`'s own provider, and checkpointing (autosave) is driven
 * entirely by the backend's idle/interval scan
 * (`app/wiki/coedit_ws.py:_scan_and_checkpoint_local_rooms`) plus the
 * last-participant-leave path (`app/api/coedit.py`) — there is no
 * client-triggered checkpoint call anymore. This hook's whole job is: open
 * the connection when `enabled`, tear it down on path change/unmount, and
 * surface connection status. Comment/source highlight data and the
 * scroll/anchor imperative handle are a separate concern, passed as props
 * directly to the editor component (`components.tsx`), not through this
 * hook — same separation the CodeMirror-era architecture used.
 */

import { useEffect, useState } from "react";
import {
  createCoeditProvider,
  type CoeditProvider,
} from "@/lib/editor/provider";

export interface UseCoeditSession {
  /** Null until the WebSocket connection object exists (before that,
   * nothing to mount the editor from). Existing doesn't mean *connected* —
   * see `connected` — `y-websocket` reconnects under this same instance,
   * so the editor only needs to remount when this identity changes (path
   * change), not on every transient disconnect. */
  conn: CoeditProvider | null;
  /** True once the WebSocket handshake completes and the initial Yjs sync
   * finishes. False during (re)connect — render a "Connecting…" state. */
  connected: boolean;
  /** Refused at connect time (no read permission) or a write attempt was
   * rejected mid-session (a write-permission revocation — see
   * `app/api/coedit.py`'s `_PermissionCheckedChannel`) — both close with
   * code 1008, indistinguishable from here on purpose; either way the
   * connection is over and the user needs to know why. Null clears on a
   * fresh connect attempt. */
  connectError: string | null;
  /** Peer count from Yjs Awareness, including self. */
  participantCount: number;
}

export function useCoeditSession(opts: {
  path: string;
  enabled: boolean;
}): UseCoeditSession {
  const { path, enabled } = opts;
  const [conn, setConn] = useState<CoeditProvider | null>(null);
  const [connected, setConnected] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);
  const [participantCount, setParticipantCount] = useState(1);

  useEffect(() => {
    if (!enabled) {
      setConn(null);
      setConnected(false);
      return;
    }
    const created = createCoeditProvider(path);
    setConn(created);
    setConnected(false);
    setConnectError(null);

    const onStatus = ({ status }: { status: string }) =>
      setConnected(status === "connected");
    const onClose = (event: CloseEvent | null) => {
      // 1008 = policy violation — the backend's permission gate
      // (app/api/coedit.py) closes with this code both at connect time
      // (no read permission) and mid-session (a write attempt after
      // permission was revoked).
      if (event?.code === 1008) {
        setConnectError("You don't have permission to edit this page.");
      }
    };
    const onAwarenessChange = () => {
      setParticipantCount(created.provider.awareness.getStates().size);
    };
    created.provider.on("status", onStatus);
    created.provider.on("connection-close", onClose);
    created.provider.awareness.on("change", onAwarenessChange);

    return () => {
      created.provider.off("status", onStatus);
      created.provider.off("connection-close", onClose);
      created.provider.awareness.off("change", onAwarenessChange);
      created.destroy();
    };
  }, [path, enabled]);

  return { conn, connected, connectError, participantCount };
}
