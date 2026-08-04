/** Rules every embedded media type shares, so a second one need not import
 * from the image node. Width rides a `#w=<int>` src fragment, not a schema
 * attr, because the codec keeps a src verbatim but drops unknown attrs. */

function stripFragment(src: string): string {
  const hash = src.indexOf("#");
  return hash === -1 ? src : src.slice(0, hash);
}

/** Largest upload the server stores, mirroring `media_upload.UPLOAD_CAP_BYTES`.
 * Checked here so an oversized file is refused at once, not after the upload. */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

/** How that cap reads to a person, in the server's own units. */
export const MAX_UPLOAD_LABEL = "10 MiB";

/** A base that exists only to resolve against. Any origin works, since the
 * question is whether `src` escapes whatever base it is resolved from. */
const RESOLUTION_BASE = "http://same-origin.invalid";

/** Whether `src` stays on the origin it resolves from, mirroring
 * `media_store.is_same_origin_src`. Resolved rather than inspected, because
 * the parser is what decides the request the browser makes. */
export function isSameOriginSrc(src: string): boolean {
  const candidate = stripFragment(src).trim();
  // An empty reference resolves to the base itself, which would pass.
  if (!candidate) return false;
  try {
    return new URL(candidate, RESOLUTION_BASE).origin === RESOLUTION_BASE;
  } catch {
    return false;
  }
}

/** The src with its fragment removed, which is what actually gets requested. */
export function srcWithoutFragment(src: string): string {
  return stripFragment(src);
}

/** Read the `w=<int>` width hint from a src's fragment, or null when absent. */
export function parseMediaWidth(src: string): number | null {
  const hash = src.indexOf("#");
  if (hash === -1) return null;
  for (const part of src.slice(hash + 1).split("&")) {
    const match = /^w=(\d+)$/.exec(part);
    if (match) return Number.parseInt(match[1]!, 10);
  }
  return null;
}

/** Return `src` with its `#w=<int>` set to `width`, replacing any existing
 * width token and preserving every other fragment part. */
export function withMediaWidth(src: string, width: number): string {
  const base = stripFragment(src);
  const hash = src.indexOf("#");
  const fragment = hash === -1 ? "" : src.slice(hash + 1);
  const parts = fragment
    .split("&")
    .filter((part) => part.length > 0 && !/^w=\d+$/.test(part));
  parts.push(`w=${width}`);
  return `${base}#${parts.join("&")}`;
}
