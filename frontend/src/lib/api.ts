const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public data?: unknown,
  ) {
    super(message);
  }
}

export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const isAbsolute = /^https?:\/\//i.test(path);
  const url = isAbsolute ? path : `${BASE}${path}`;
  const headers: HeadersInit = {
    "content-type": "application/json",
    ...(init?.headers ?? {}),
  };
  const credentials: RequestCredentials =
    init?.credentials ?? (isAbsolute ? "omit" : "include");
  const res = await fetch(url, {
    ...init,
    headers,
    credentials,
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    let data: unknown;
    try {
      data = await res.json();
      const body = data as { error?: string };
      if (body?.error) message = body.error;
    } catch {
      // ignore
    }
    throw new ApiError(res.status, message, data);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/** Like {@link apiFetch} but for binary downloads (CSV exports, etc.):
 * same base URL, credentials, and `{error}`-envelope handling, but resolves
 * the body as a Blob instead of JSON. Keeps binary endpoints on the same
 * network seam. */
export async function apiFetchBlob(
  path: string,
  init?: RequestInit,
): Promise<Blob> {
  const isAbsolute = /^https?:\/\//i.test(path);
  const url = isAbsolute ? path : `${BASE}${path}`;
  const credentials: RequestCredentials =
    init?.credentials ?? (isAbsolute ? "omit" : "include");
  const res = await fetch(url, { ...init, credentials });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { error?: string };
      if (body?.error) message = body.error;
    } catch {
      // non-JSON error body — keep the generic message
    }
    throw new ApiError(res.status, message);
  }
  return res.blob();
}

/** Like {@link apiFetch} but POSTs a raw binary body (image bytes, etc.):
 * same base URL, credentials, and `{error}`-envelope handling, but sends the
 * `Blob` verbatim under its real `contentType` instead of a JSON body. Keeps
 * binary uploads on the same network seam. The response is parsed as JSON. */
export function apiUpload<T>(
  path: string,
  body: Blob,
  contentType: string,
): Promise<T> {
  return apiFetch<T>(path, {
    method: "POST",
    headers: { "content-type": contentType },
    body,
  });
}

/** Resolves `path` against `BASE` the same way {@link apiFetch} does, but as
 * a `ws://`/`wss://` URL for a `new WebSocket(...)` caller. Kept here (not in
 * the coedit-specific client) so `BASE`'s resolution logic — relative vs. an
 * absolute `NEXT_PUBLIC_API_BASE` override — lives in exactly one place. */
export function apiSocketUrl(path: string): string {
  const isAbsolute = /^https?:\/\//i.test(BASE);
  if (isAbsolute) {
    return `${BASE}${path}`.replace(/^http/, "ws");
  }
  const proto =
    typeof window !== "undefined" && window.location.protocol === "https:"
      ? "wss"
      : "ws";
  const host = typeof window !== "undefined" ? window.location.host : "";
  return `${proto}://${host}${BASE}${path}`;
}

/** SSE-style streaming POST. Parses ``data: {...json}\n\n`` frames and
 * dispatches them through ``onEvent``. Pre-stream HTTP errors come back as
 * ``ApiError`` (matching ``apiFetch``). The promise resolves when the server
 * closes the connection. */
export async function apiStream(
  path: string,
  init: RequestInit,
  onEvent: (data: unknown) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      accept: "text/event-stream",
      ...(init.headers ?? {}),
    },
    credentials: "include",
    signal,
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { error?: string };
      if (body?.error) message = body.error;
    } catch {
      // ignore
    }
    throw new ApiError(res.status, message);
  }
  const reader = res.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  let buf = "";
  // Loop reads chunks; SSE frames are separated by a blank line (\n\n).
  // Within a frame, "data:" lines are concatenated and JSON-parsed.
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      const dataLines = frame
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).trimStart());
      if (dataLines.length === 0) continue;
      try {
        onEvent(JSON.parse(dataLines.join("\n")));
      } catch {
        // skip malformed frame
      }
    }
  }
}
