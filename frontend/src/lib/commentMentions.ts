/** @mentions inside a comment body.
 *
 * Mentions are persisted inline in the stored body as a canonical token:
 *
 *     @[Display Name](mention:<user_id>)
 *
 * The composer works in the human form ("@Display Name"); we (de)tokenize at
 * the edit/save boundary so editing stays readable while storage keeps the
 * user id. Rendering parses the tokens into chips. A later notification pass
 * can recover the mentioned user ids by parsing the body — no separate store.
 */

const TOKEN_SOURCE = /@\[([^\]]+)\]\(mention:([^)]+)\)/g;

/** Stored body → display text + a `"@Name" → userId` map. */
export function detokenizeMentions(body: string): {
  text: string;
  map: Record<string, string>;
} {
  const map: Record<string, string> = {};
  const text = body.replace(
    new RegExp(TOKEN_SOURCE),
    (_full, name: string, id: string) => {
      map[`@${name}`] = id;
      return `@${name}`;
    },
  );
  return { text, map };
}

/** Display text + map → stored body, re-inserting a token for each known
 * mention. Longest display names first so "@Bo" can't clobber "@Bo Yang". */
export function tokenizeMentions(
  text: string,
  map: Record<string, string>,
): string {
  const displays = Object.keys(map).sort((a, b) => b.length - a.length);
  let out = text;
  for (const disp of displays) {
    const id = map[disp];
    if (!id) continue;
    // Only at a start-or-whitespace `@` boundary, so we never rewrite an `@name`
    // that's part of something else (e.g. the local part of an email address).
    const escaped = disp.slice(1).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    out = out.replace(
      new RegExp(`(?:^|(?<=\\s))@${escaped}(?=\\s|$)`, "g"),
      `@[${disp.slice(1)}](mention:${id})`,
    );
  }
  return out;
}

export type BodySegment =
  | { kind: "text"; text: string }
  | { kind: "mention"; name: string; userId: string };

/** Split a stored body into text and mention segments for rendering. */
export function parseBody(body: string): BodySegment[] {
  const segs: BodySegment[] = [];
  const re = new RegExp(TOKEN_SOURCE);
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(body)) !== null) {
    if (m.index > last)
      segs.push({ kind: "text", text: body.slice(last, m.index) });
    segs.push({ kind: "mention", name: m[1]!, userId: m[2]! });
    last = m.index + m[0].length;
  }
  if (last < body.length) segs.push({ kind: "text", text: body.slice(last) });
  return segs;
}

/** The in-progress `@query` immediately before the caret, or null if the caret
 * isn't in a mention token. A mention starts at the string start or after
 * whitespace, and runs up to the caret with no whitespace/`@` inside. */
export function activeMentionQuery(
  value: string,
  caret: number,
): { query: string; start: number } | null {
  const before = value.slice(0, caret);
  const m = /(?:^|\s)@([^\s@]*)$/.exec(before);
  if (!m) return null;
  return { query: m[1]!, start: caret - m[1]!.length - 1 };
}
