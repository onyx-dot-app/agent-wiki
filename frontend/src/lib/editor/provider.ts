"use client";

/**
 * The onyx-editor live-doc connection: one Y.Doc + one `y-websocket`
 * `WebsocketProvider` per open page, talking to the backend's
 * `WebSocket /api/coedit/ws/{path}` endpoint (`app/api/coedit_ws.py`).
 *
 * `y-websocket`'s provider builds its connection URL as `serverUrl +
 * "/" + roomname` (verified against its source — no query-param option
 * without a custom provider), which is why the backend route takes the
 * wiki path as a URL segment rather than `?path=`. The room name must be
 * percent-encoded per path segment (spaces, etc. are legal in a wiki path
 * but not in a raw URL) — the backend route decodes it back via FastAPI's
 * `:path` converter.
 *
 * Auth: the WS handshake is same-origin and carries the session cookie
 * automatically (browsers attach cookies to a `new WebSocket(...)` request
 * exactly like a normal same-origin fetch) — no separate token needed.
 */

import { WebsocketProvider } from "y-websocket";
import * as Y from "yjs";

export interface CoeditProvider {
  ydoc: Y.Doc;
  provider: WebsocketProvider;
  destroy: () => void;
}

function wsBaseUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/coedit/ws`;
}

function encodeRoomName(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

export function createCoeditProvider(path: string): CoeditProvider {
  const ydoc = new Y.Doc();
  const provider = new WebsocketProvider(
    wsBaseUrl(),
    encodeRoomName(path),
    ydoc,
    { connect: true },
  );
  return {
    ydoc,
    provider,
    destroy: () => {
      provider.destroy();
      ydoc.destroy();
    },
  };
}
