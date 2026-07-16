/** Max ms between outbound cursor/typing pings; intermediates are coalesced. */
export const CURSOR_THROTTLE_MS = 80;

/** Ms of keystroke silence before sending a "stopped typing" ping. */
export const TYPING_IDLE_MS = 1500;

/** Ms without a ping before a peer's "typing" badge is cleared (covers crash / lost tab). */
export const TYPING_EXPIRY_MS = 4000;
