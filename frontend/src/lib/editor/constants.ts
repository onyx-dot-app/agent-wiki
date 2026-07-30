/** Opal-ish hues that read on both themes, one per peer slot. */
export const PEER_COLORS = [
  "#e5484d",
  "#0090ff",
  "#30a46c",
  "#f76b15",
  "#8e4ec6",
  "#e5b000",
  "#00a2c7",
  "#e93d82",
];

/** Max ms between outbound cursor/typing pings; intermediates are coalesced. */
export const CURSOR_THROTTLE_MS = 80;

/** Ms of keystroke silence before sending a "stopped typing" ping. */
export const TYPING_IDLE_MS = 1500;

/** Ms without a ping before a peer's "typing" badge is cleared (covers crash / lost tab). */
export const TYPING_EXPIRY_MS = 4000;

/** Ms of doc-change silence before an autosave checkpoint fires.
 *
 * A checkpoint is a git commit (and, when a concurrent change has landed, a
 * 3-way merge), so the engine is built around one commit per editing session,
 * not per keystroke — see `app/wiki/coedit_checkpoint.py`. Durability doesn't
 * ride on this: every op is already in Postgres, and the hook checkpoints on
 * teardown and `pagehide`. This only governs how fresh git is for everyone
 * else while a tab stays open, so seconds of lag costs nothing and a commit
 * per pause costs history. */
export const AUTOSAVE_IDLE_MS = 15000;

/** Hard cap: force a checkpoint at least this often even under continuous typing.
 * A backstop against a marathon session leaving git far behind, not an autosave
 * interval — the server's own scan (idle 5min / overdue 15min) sits behind it. */
export const AUTOSAVE_MAX_INTERVAL_MS = 180000;

/** Ms of no local activity (edits, caret moves) before the editor auto-blurs.
 * Blurring routes through the normal focus-loss path, so the idle user's
 * caret clears for peers and their presence label drops to "viewing" —
 * an untouched tab can't hold an "editing" caret forever. Generous enough
 * that nobody actively working gets interrupted: a false blur costs the
 * user a click back in, a stale "editing" label only costs peers confusion. */
export const IDLE_UNFOCUS_MS = 10 * 60 * 1000;

/** Ms to wait before re-opening a dropped co-edit SSE stream. */
export const STREAM_RECONNECT_MS = 3000;
