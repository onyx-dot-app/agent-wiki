/** The one place a peer's identity colour is decided.
 *
 * A peer shows up twice on a page — as a caret in the document
 * (`presence.ts` → `yCursorPlugin`) and as a presence chip's ring
 * (`components/wiki/PresenceAvatars.tsx`) — and those two have to be the
 * same colour, or the chip stops identifying whose caret is whose, which is
 * the only thing the colour is for. Keeping one palette and one keying rule
 * in one module is what makes that true; two lists drift the moment either
 * side is edited.
 *
 * Keyed on `userId`, never on a position in a list: a peer's colour has to
 * hold still while other people come and go around them.
 */

/** Opal tokens rather than literals, per CLAUDE.md. */
const IDENTITY_COLORS = [
  "var(--neon-cyan-50)",
  "var(--neon-yellow-50)",
  "var(--neon-lime-60)",
  "var(--neon-magenta-50)",
  "var(--purple-50)",
];

/** A stable colour for `userId` — same input, same colour, for as long as the
 * palette is unchanged. Distinct users can collide once there are more of
 * them than colours; a collision costs a moment of ambiguity, whereas a
 * colour that moves around costs the reader the mapping entirely. */
export function colorFor(userId: string): string {
  let h = 0;
  for (let i = 0; i < userId.length; i++)
    h = (h * 31 + userId.charCodeAt(i)) | 0;
  return IDENTITY_COLORS[Math.abs(h) % IDENTITY_COLORS.length]!;
}
