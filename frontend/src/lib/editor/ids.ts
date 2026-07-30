/** Opaque client-side ids that must not depend on a secure context.
 *
 * `crypto.randomUUID()` is only defined in a secure context — HTTPS, or
 * `localhost`. Reached over plain HTTP on a LAN address (a dev stack shared with
 * a teammate, a self-hosted instance on an internal IP), it is `undefined`, and
 * calling it throws `TypeError: crypto.randomUUID is not a function`.
 *
 * That wasn't hypothetical: it made *every* save fail with
 * "Couldn't save" for anyone not on localhost, throwing before the request was
 * ever sent — which is why no server log ever showed it, and why it reproduced
 * for one person and not another on the same build.
 *
 * `crypto.getRandomValues` has no secure-context requirement, so it's the first
 * fallback; `Math.random` is the last resort. None of these ids are secrets or
 * durable keys — a request id correlates a checkpoint with its ack over one
 * socket, and a client id distinguishes carets within one session — so
 * collision-resistance is all that's needed, not cryptographic quality.
 */

function randomHex(bytes: number): string | null {
  const c = globalThis.crypto;
  if (!c || typeof c.getRandomValues !== "function") return null;
  const buf = new Uint8Array(bytes);
  c.getRandomValues(buf);
  return Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** A unique-enough opaque id, in any browsing context. */
export function opaqueId(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  const hex = randomHex(16);
  if (hex) return hex;
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}
