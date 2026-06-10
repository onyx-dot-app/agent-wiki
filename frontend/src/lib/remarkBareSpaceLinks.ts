/**
 * Re-linkify Markdown link/image targets that contain spaces.
 *
 * CommonMark only linkifies `[text](dest)` when `dest` has no spaces (or is
 * angle-bracketed), so internal wiki links — whose paths routinely contain
 * spaces — render as literal text. This remark plugin rewrites such targets to
 * `%20` form at render time, so authors never need angle brackets. Output still
 * flows through react-markdown's `defaultUrlTransform`, so unsafe schemes stay
 * neutralized.
 *
 * Runs on mdast `text` nodes only; `inlineCode` / `code` hold their content in
 * `value`, not child text nodes, so link-like sequences in code are untouched.
 */

interface MdastNode {
  type: string;
  value?: string;
  children?: MdastNode[];
  url?: string;
  alt?: string | null;
  title?: string | null;
}

// `[label](dest)` / `![alt](dest)` where dest holds >=1 space and no `()<>`. The
// lookahead does the has-a-space test so the destination is a single bounded
// quantifier with no inter-group backtracking (ReDoS-safe). Excluding `<` leaves
// already-angle-bracketed destinations unmatched (CommonMark handles those).
const SPACE_LINK =
  /(!?)\[([^\]\n]{1,500})\]\((?=[^()<>\n]{0,2048}\s)([^()<>\n]{1,2048})\)/g;

// A trailing CommonMark link title: ` "..."` or ` '...'`.
const TRAILING_TITLE = /\s+(?:"([^"]*)"|'([^']*)')\s*$/;

function encodeTarget(dest: string): string {
  // encodeURI handles spaces + non-ASCII + unsafe chars while preserving path
  // structure and `#`/`?` (so heading-anchor / query links keep working); the
  // trailing replace restores any already-valid %XX escape it double-encoded.
  return encodeURI(dest).replace(/%25([0-9A-Fa-f]{2})/g, "%$1");
}

function relinkify(value: string): MdastNode[] | null {
  // No inline-link syntax → skip the regex entirely (the common case, and bounds
  // adversarial text that has no `](`).
  if (!value.includes("](")) return null;
  SPACE_LINK.lastIndex = 0;
  const out: MdastNode[] = [];
  let cursor = 0;
  let matched = false;
  let match: RegExpExecArray | null;
  while ((match = SPACE_LINK.exec(value)) !== null) {
    matched = true;
    const [full, bang, label, rawDest] = match;
    if (match.index > cursor) {
      out.push({ type: "text", value: value.slice(cursor, match.index) });
    }
    let dest = rawDest.trim();
    let title: string | null = null;
    const titleMatch = TRAILING_TITLE.exec(dest);
    if (titleMatch) {
      dest = dest.slice(0, titleMatch.index).trim();
      title = titleMatch[1] ?? titleMatch[2];
    }
    const url = encodeTarget(dest);
    if (bang === "!") {
      out.push({ type: "image", url, alt: label, title });
    } else {
      out.push({
        type: "link",
        url,
        title,
        children: [{ type: "text", value: label }],
      });
    }
    cursor = match.index + full.length;
  }
  if (!matched) return null;
  if (cursor < value.length) {
    out.push({ type: "text", value: value.slice(cursor) });
  }
  return out;
}

function transform(node: MdastNode): void {
  const children = node.children;
  if (!children) return;
  const next: MdastNode[] = [];
  for (const child of children) {
    if (child.type === "text" && typeof child.value === "string") {
      const replaced = relinkify(child.value);
      if (replaced) {
        next.push(...replaced);
        continue;
      }
    } else if (child.children) {
      transform(child);
    }
    next.push(child);
  }
  node.children = next;
}

export function remarkBareSpaceLinks() {
  return (tree: MdastNode): void => transform(tree);
}
