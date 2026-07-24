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

// Cursor/typing throttling, autosave timers, and reconnect backoff are no
// longer client-owned constants — Yjs Awareness handles cursor/typing
// natively, checkpointing is entirely server-driven (idle/interval scan +
// last-participant-leave, see app/wiki/coedit_ws.py), and `y-websocket`'s
// own provider owns reconnect backoff internally. Removed rather than kept
// as now-unused exports.
