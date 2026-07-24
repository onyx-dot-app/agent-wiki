"use client";

/**
 * The live-doc connection: one Y.Doc + one `y-websocket` `WebsocketProvider`
 * per open page, talking to the backend's `WebSocket /api/coedit/ws/{path}`
 * endpoint (`app/api/coedit.py`).
 *
 * `y-websocket`'s provider builds its connection URL as `serverUrl + "/" +
 * roomname` (verified against its source — no query-param option without a
 * custom provider), which is why the backend route takes the wiki path as a
 * URL segment rather than `?path=`. The room name is percent-encoded per
 * path segment (spaces, etc. are legal in a wiki path but not in a raw URL)
 * — the backend route decodes it back via FastAPI's `:path` converter.
 *
 * `serverUrl` goes through `apiSocketUrl` (`@/lib/api.ts`) rather than a
 * hand-rolled `window.location.host` — that's the one place `BASE`'s
 * resolution (relative vs. an absolute `NEXT_PUBLIC_API_BASE` override) is
 * meant to live, and only `apiSocketUrl` handles the override case.
 *
 * Auth: the WS handshake is same-origin and carries the session cookie
 * automatically (browsers attach cookies to a `new WebSocket(...)` request
 * exactly like a normal same-origin fetch) — no separate token needed.
 */

import { WebsocketProvider } from "y-websocket";
import * as Y from "yjs";
import { apiSocketUrl } from "@/lib/api";

export interface CoeditProvider {
  ydoc: Y.Doc;
  provider: WebsocketProvider;
  destroy: () => void;
}

function encodeRoomName(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

export function createCoeditProvider(path: string): CoeditProvider {
  const ydoc = new Y.Doc();
  const provider = new WebsocketProvider(
    apiSocketUrl("/coedit/ws"),
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
