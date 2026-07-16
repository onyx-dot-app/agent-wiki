/** Shared types for the co-editing subsystem. */

/** One range-replacement edit: replace `[from, to)` (UTF-16) with `insert`. */
export interface CoeditChange {
  from: number;
  to: number;
  insert: string;
}

/** A session participant as returned by join / presence frames. */
export interface CoeditParticipant {
  user_id: string;
  user_display: string;
  joined_at: string;
  last_seen_at: string;
}

/** A peer's live caret/selection, from their latest `cursor` frame. Offsets are
 * UTF-16 code units (JS-native), collapsed (anchor === head) = caret. */
export interface CoeditPeer {
  user_id: string;
  user_display: string;
  anchor: number;
  head: number;
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
      anchor: number;
      head: number;
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
  /** Autosave state, for a "Saving…/Saved/Couldn't save" indicator. */
  saveStatus: "saved" | "saving" | "error";
  /** Register a handler the hook calls with each inbound `op`/`resync` frame. */
  onServerFrame: (handler: ((frame: CoeditFrame) => void) | null) => void;
  /** Editor reports its current doc so the hook's `buffer` mirror stays fresh;
   * also arms the autosave idle/max-interval timers. */
  reportDoc: (doc: string) => void;
  /** Register the editor's "flush pending ops" fn, awaited before every
   * checkpoint (autosave or `leave`). */
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
  /** Flush pending ops, checkpoint the buffer to git, then leave the session.
   * Not a Save button — called on unmount/path-change/entering history. */
  leave: () => Promise<void>;
}
