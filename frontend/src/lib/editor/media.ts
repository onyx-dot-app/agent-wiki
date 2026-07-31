/** Rules every embedded media type shares, so a second one need not import
 * from the image node. Width rides a `#w=<int>` src fragment, not a schema
 * attr, because the codec keeps a src verbatim but drops unknown attrs. */

function stripFragment(src: string): string {
  const hash = src.indexOf("#");
  return hash === -1 ? src : src.slice(0, hash);
}

/** Whether `src` loads from this origin, mirroring
 * `media_store.is_same_origin_src`. Attrs arrive from any collaborator, so a
 * scheme or `//` must never become a request. */
export function isSameOriginSrc(src: string): boolean {
  const trimmed = stripFragment(src).trim();
  if (!trimmed || trimmed.startsWith("//")) return false;
  return !trimmed.split("/", 1)[0]!.includes(":");
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
