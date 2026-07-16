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

/** Ms of doc-change silence before an autosave checkpoint fires. */
export const AUTOSAVE_IDLE_MS = 2000;

/** Hard cap: force a checkpoint at least this often even under continuous typing. */
export const AUTOSAVE_MAX_INTERVAL_MS = 30000;
