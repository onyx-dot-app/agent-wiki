/** Shared types for the co-editing subsystem. */

/** One range-replacement edit: replace `[from, to)` (UTF-16) with `insert`. */
export interface CoeditChange {
  from: number;
  to: number;
  insert: string;
}

/** A session participant as returned by join / presence frames. The live
 * session is joined by everyone on the page; `caret_active` (true while they
 * have a caret placed in the text) is what separates editors from viewers —
 * event-driven, no decay: set by cursor placement / edits, cleared on editor
 * blur / hidden tab, gone with the row on leave. */
export interface CoeditParticipant {
  user_id: string;
  user_display: string;
  joined_at: string;
  last_seen_at: string;
  last_edited_at: string | null;
  caret_active: boolean;
}

/** A peer's live caret/selection, from their latest `cursor` frame. Offsets are
 * UTF-16 code units (JS-native), collapsed (anchor === head) = caret. `seq` is
 * a client-local counter bumped per received frame — it lets the editor tell a
 * fresh frame (adopt its raw offsets) from an entry merely re-sent when the
 * peer array was rebuilt (keep the position it has mapped through doc
 * changes). */
export interface CoeditPeer {
  user_id: string;
  user_display: string;
  anchor: number;
  head: number;
  seq: number;
}

/** Snapshot returned by join / session (the live buffer + roster). */
export interface CoeditSession {
  session_id: number;
  buffer: string;
  version: number;
  base_sha: string | null;
  participants: CoeditParticipant[];
}

/** Frames pushed over the SSE stream (`coedit_channel.py`). */
export type CoeditFrame =
  | { type: "presence"; session_id: number; participants: CoeditParticipant[] }
  | {
      type: "op";
      session_id: number;
      version: number;
      changes: CoeditChange[];
      author: string | null;
      client_id: string | null;
    }
  | {
      type: "cursor";
      session_id: number;
      user_id: string;
      user_display: string;
      // Null anchor/head = the sender cleared their caret (editor blur /
      // hidden tab) — drop it and flip their presence label to "viewing".
      anchor: number | null;
      head: number | null;
      typing: boolean;
    }
  | { type: "resync"; session_id: number; version: number };

/** One logged operation (one version bump) — mirrors the SSE `op` frame. */
export interface CoeditOperation {
  version: number;
  author: string;
  client_id: string | null;
  changes: CoeditChange[];
}

/** Ops after `since_version` (oldest first) + the current head version. Used to
 * rebase un-acked local edits after a stale op / gap (`GET /coedit/ops`). */
export interface CoeditOps {
  session_id: number;
  current_head_version: number;
  ops: CoeditOperation[];
}

/** What the editor needs to run collab, available once the session is joined. */
export interface CoeditSessionHandle {
  id: number;
  clientId: string;
  startVersion: number;
  startDoc: string;
}

/** Return type of `useCoeditSession`. */
export interface UseCoeditSession {
  /** True once the join handshake completes and the SSE stream is live. */
  active: boolean;
  /** Read-only mirror of the editor's doc, for non-editor UI. */
  buffer: string;
  /** All current session participants (including self). */
  participants: CoeditParticipant[];
  /** user_ids of peers currently typing (excludes self). */
  typing: string[];
  /** Peers' live carets/selections (excludes self), for editor decorations. */
  peers: CoeditPeer[];
  /** Set once joined; the editor mounts its collab document from it. */
  session: CoeditSessionHandle | null;
  /** Set when the join handshake itself fails (network error, 4xx/5xx,
   * expired auth) — `session` stays null and stays null forever unless the
   * caller shows this and offers `retryJoin`. A stream drop *after* a
   * successful join doesn't set this (the session is already usable). */
  joinError: string | null;
  /** Clears `joinError` and re-runs the join handshake. */
  retryJoin: () => void;
  /** Autosave state, for a "Saving…/Saved/Couldn't save" indicator. */
  saveStatus: "saved" | "saving" | "error";
  /** Register a handler the hook calls with each inbound `op`/`resync` frame. */
  onServerFrame: (handler: ((frame: CoeditFrame) => void) | null) => void;
  /** Editor reports its current doc so the hook's `buffer` mirror stays fresh;
   * also arms the autosave idle/max-interval timers. */
  reportDoc: (doc: string) => void;
  /** Register the editor's "flush pending ops" fn, awaited before every
   * checkpoint (autosave or session teardown). */
  registerFlush: (fn: (() => Promise<void>) | null) => void;
  /** Register the editor's "replace whole doc" fn (template pick / blank). */
  registerSetDoc: (fn: ((text: string) => void) | null) => void;
  /** Replace the document (goes through the editor as an edit). No-op until
   * the editor has mounted. */
  setDoc: (text: string) => void;
  /** Report the local caret/selection so peers see presence. `isEdit=true`
   * marks "typing…" + arms its auto-clear; `isEdit=false` (a caret move) reports
   * position without changing the typing state. Throttled + coalesced. */
  reportSelection: (anchor: number, head: number, isEdit: boolean) => void;
  /** Report that the local caret is gone (editor blur / hidden tab) — peers
   * drop it and presence flips us to "viewing". Sent immediately (clears are
   * rare), dropping any queued position so a throttled send can't resurrect
   * the caret. */
  reportCaretCleared: () => void;
}
