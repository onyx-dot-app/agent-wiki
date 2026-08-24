"use client";

/** React hook that owns a page's live-session lifecycle + presence —
 * Yjs-native replacement for `lib/editor/hooks.ts`'s `useCoeditSession`.
 * Return shape mirrors that hook's contract as closely as sensible (see
 * `UseCoeditSession` below) so `FileView.tsx`'s surrounding logic
 * (autosave indicator, presence bar, template-picking, connecting/error
 * UI) needs re-wiring, not a rewrite.
 *
 * Most of the old hook's internals don't carry over — Yjs's own CRDT sync
 * (`Collaboration`) and Awareness protocol (`presenceExtension`'s
 * `yCursorPlugin`) already do what that hook hand-rolled: op application,
 * caret-epoch tracking, cursor throttling, buffer mirroring. This hook's
 * job shrinks to connection lifecycle, deriving React-observable presence
 * state from Awareness (which is a plain event emitter, not React state),
 * and autosave.
 *
 * On `enabled` it opens the session's WebSocket for `path`, binding a
 * `Y.Doc`/`Awareness` pair this hook owns for the connection's lifetime —
 * pass them to `<TipTapEditor doc={coedit.doc} awareness={coedit.awareness}
 * .../>`. Teardown (checkpoint, then close, in that order — closing before
 * a final edit's checkpoint has landed would let the server's last-leave
 * forced commit race ahead of it) happens entirely inside the hook's own
 * effect cleanup.
 *
 * If the join handshake itself fails, `active` stays false forever and
 * `joinError` is set — the caller must show it and offer `retryJoin`.
 */
import type { Editor } from "@tiptap/core";
import {
  relativePositionToAbsolutePosition,
  ySyncPluginKey,
} from "@tiptap/y-tiptap";
import { useCallback, useEffect, useRef, useState } from "react";
import { Awareness } from "y-protocols/awareness";
import * as Y from "yjs";
import { ApiError } from "@/lib/api";
import { colorFor } from "@/lib/editor/identityColor";
import {
  CHECKPOINT_TIMED_OUT,
  checkpointSession,
  closeSession,
  connectSession,
  type CoeditParticipant,
} from "@/lib/editor/svc";

/** Ms of doc-change silence before an autosave checkpoint fires. The
 * server's own periodic scan (`app/tasks/coedit_checkpoint.py`) is a much
 * coarser backstop (idle 300s / overdue 900s) — this is what makes the
 * "Saved" indicator feel immediate rather than waiting on that. */
const AUTOSAVE_IDLE_MS = 2000;
/** Hard cap: force a checkpoint at least this often even under continuous
 * typing. */
const AUTOSAVE_MAX_INTERVAL_MS = 30000;
/** Ms of no local awareness change before a peer's local "typing" state
 * clears. */
const TYPING_IDLE_MS = 1500;
/** Ms to wait before re-opening a dropped connection. */
const STREAM_RECONNECT_MS = 3000;
/** Reconnect backoff ceiling. Attempts double from `STREAM_RECONNECT_MS` and
 * park here, so a long outage costs one join attempt per half-minute while a
 * deploy blip still recovers in seconds. */
const RECONNECT_BACKOFF_MAX_MS = 30_000;
/** Spacing and cap for retrying a failed save.
 *
 * Every failure mode except "forbidden" is transient: the save had no live
 * socket because the connection was reconnecting, or the socket closed with the
 * request in flight. Without a retry, one unlucky autosave — 2s after you stop
 * typing, which is exactly when a reconnect is likely to be in progress — left
 * "Couldn't save" on screen until the user happened to type again and pause,
 * because `runAutoCheckpoint` clears the timers before calling and nothing
 * re-arms them.
 *
 * Capped rather than infinite because it isn't the durability mechanism: every
 * keystroke is already in `coedit_updates` before a checkpoint runs, and the
 * server's own idle scan commits the session regardless. 10 × 3s comfortably
 * outlasts a reconnect; past that the indicator should stay honest. */
const SAVE_RETRY_MS = 3000;
const SAVE_RETRY_LIMIT = 10;
/** Timeouts get their own, much smaller budget.
 *
 * `SAVE_RETRY_LIMIT` above is sized for a failure that returns *instantly*
 * ("not connected" during a reconnect), where 10 × 3s is a sensible 30-second
 * window. A `CHECKPOINT_TIMED_OUT` costs a full minute per attempt, so reusing
 * that budget would mean ~11 minutes of alternating "Saving…"/"Couldn't save".
 * One retry bounds it to about two minutes and then leaves an honest error.
 *
 * Giving up is safe, not lossy: the edits are already in `coedit_updates`, and
 * the server's periodic scan commits a dirty session on its own (5-min idle /
 * 15-min overdue). The retry only controls how fast the *indicator* recovers. */
const SAVE_TIMEOUT_RETRY_LIMIT = 1;

export interface CoeditPeer {
  /** Awareness client id — one per live connection, so two tabs from the
   * same user are two peers. Only the local connection is excluded. */
  client_id: number;
  user_id: string;
  user_display: string;
  /** The identity colour this connection advertises for its caret
   * (`components.tsx` sets it via `sessionColorFor`) — presence chips show
   * the same value so caret and chip always agree. */
  color: string;
  /** ProseMirror document positions (not markdown character offsets) —
   * pass directly to `CoeditorHandle.scrollToOffset`. `null` when the
   * peer's cursor can't currently be resolved (e.g. it points at content
   * this client hasn't received yet, or is mid-reconnect). */
  anchor: number | null;
  head: number | null;
}

export interface UseCoeditSession {
  /** True once the join handshake completes. */
  active: boolean;
  /** Bump count for `doc`/`awareness`'s identity — pass as
   * `<TipTapEditor key={coedit.connectionId}>`. Required, not cosmetic:
   * Tiptap's `useEditor` defaults to building the editor once and never
   * rebuilding it from new `doc`/`awareness` props (confirmed against the
   * installed `@tiptap/react` — `deps = []` unless the caller opts in), so
   * without a `key` forcing a full remount, a reconnect's fresh `Y.Doc`
   * would never actually reach the editor. Bumps on every fresh
   * doc/awareness pair — a reconnect lands back on the *same* session_id
   * (the session outlives any one socket), so that id alone can't be used as
   * the key the way the old hook's `session.id` was. */
  connectionId: number;
  /** This connection's Yjs doc — pass to `<TipTapEditor doc={...}>`. A
   * fresh instance every time `connectionId` bumps. */
  doc: Y.Doc;
  /** This connection's Awareness — pass to `<TipTapEditor awareness={...}>`. */
  awareness: Awareness;
  /** Whether the user may edit this page (`can_write` from the join
   * response). False joins as a pure viewer: presence + live updates flow
   * as usual, but the server drops any content/awareness change from this
   * connection. */
  canWrite: boolean;
  /** Plain-text mirror of the live doc (`editor.getText()`), for non-editor
   * UI that needs to reason about content as a string (the template
   * gallery's "is this page still blank" check) — empty until the editor
   * mounts (`onEditorReady`), not just until the session joins. */
  buffer: string;
  /** All current session participants (including self). */
  participants: CoeditParticipant[];
  /** user_ids of peers currently typing (excludes self). */
  typing: string[];
  /** Peers' live cursors (excludes self), for presence UI. */
  peers: CoeditPeer[];
  /** Set only when joining fails for a reason retrying cannot fix (today: a
   * page the live-editor codec can't encode). Transient failures — network
   * errors, a dropped connection, a deploy window — never set this: the
   * connect loop keeps retrying with backoff and reports progress through
   * `reconnectAttempts` instead. */
  joinError: string | null;
  /** False when `joinError` is a real, distinguishable reason retrying
   * won't fix (today: a page the live-editor codec can't encode) — the
   * caller should hide/disable `retryJoin` and say so instead of offering
   * a Retry that can never succeed. True (the default) for every other
   * failure: a network error, expired auth, a plain dropped connection —
   * all worth retrying. */
  joinErrorRetryable: boolean;
  /** Clears `joinError` and re-runs the join handshake. */
  retryJoin: () => void;
  /** 0 while connected; otherwise how many reconnect attempts have failed
   * since the connection dropped (1 the moment the drop is noticed).
   * Render this with precedence over `saveStatus`: during an outage the
   * save machinery's failures are consequences of the drop, and recovery
   * is already running — the truthful label is "Reconnecting…", with
   * sterner guidance once the count grows. */
  reconnectAttempts: number;
  /** Autosave state, for a "Saving…/Saved/Couldn't save" indicator.
   *
   * "unconfirmed" is distinct from "error" on purpose: the save's
   * acknowledgement never came back, which says nothing about whether the
   * commit happened — the server may well have committed and only the reply
   * gone missing. Rendering that as a failure would be a claim we can't
   * support, so it gets its own label. */
  saveStatus: "saved" | "saving" | "error" | "unconfirmed";
  /** Detail for the current `saveStatus`, when there is any.
   *
   * For "error", why the save failed — the server's own reason ("forbidden",
   * the task's failure) or "not connected" when there was no live socket to
   * ask. Surfaced because a bare "Couldn't save" is undiagnosable: three
   * unrelated faults produce it, and the reason was being swallowed by a
   * `catch {}` so it reached neither the UI nor the console.
   *
   * For "unconfirmed", what happens next rather than what went wrong. Both are
   * rendered as a suffix to the status label, so neither should repeat it. */
  saveError: string | null;
  /** Wire the underlying Tiptap `Editor` instance once it mounts — needed
   * to resolve peer cursor positions and to drive `setDoc`. Pass directly
   * as `<TipTapEditor onEditorReady={coedit.onEditorReady}>`. */
  onEditorReady: (editor: Editor) => void;
  /** Replace the whole document with plain text, split into paragraphs on
   * blank lines — used by template-picking. A deliberate simplification:
   * there's no client-side markdown parser wired for this editor's schema
   * (parsing markdown happens once, server-side, at session open — see
   * `app/wiki/markdown_yjs.py`), so a template's headings/lists/tables
   * apply as plain paragraphs rather than their real structure. Applies as
   * a normal local edit (propagates via the live doc like any other
   * change), matching the old hook's `setDoc` semantics. */
  setDoc: (text: string) => void;
}

/** Peer cursor positions, resolved against the *current* doc via the sync
 * binding's relative-position mapping — same mechanism `yCursorPlugin`
 * itself uses internally to render carets (see `@tiptap/y-tiptap`), reused
 * here for presence UI outside the editor (click-an-avatar-to-scroll). */
function derivePeers(editor: Editor, awareness: Awareness): CoeditPeer[] {
  const ystate = ySyncPluginKey.getState(editor.state);
  if (!ystate) return [];
  const peers: CoeditPeer[] = [];
  awareness.getStates().forEach((state, clientId) => {
    if (clientId === awareness.clientID) return; // never render self as a peer
    const user = (
      state as
        | { user?: { id?: string; name?: string; color?: string } }
        | undefined
    )?.user;
    const cursor = (
      state as { cursor?: { anchor: unknown; head: unknown } } | undefined
    )?.cursor;
    if (!user?.id) return;
    let anchor: number | null = null;
    let head: number | null = null;
    if (cursor) {
      anchor = relativePositionToAbsolutePosition(
        ystate.doc,
        ystate.type,
        Y.createRelativePositionFromJSON(cursor.anchor),
        ystate.binding.mapping,
      );
      head = relativePositionToAbsolutePosition(
        ystate.doc,
        ystate.type,
        Y.createRelativePositionFromJSON(cursor.head),
        ystate.binding.mapping,
      );
    }
    peers.push({
      client_id: clientId,
      user_id: user.id,
      user_display: user.name ?? "Anonymous",
      color: user.color ?? colorFor(user.id),
      anchor,
      head,
    });
  });
  return peers;
}

function deriveTyping(awareness: Awareness): string[] {
  const typing: string[] = [];
  awareness.getStates().forEach((state, clientId) => {
    if (clientId === awareness.clientID) return;
    const user = (state as { user?: { id?: string } } | undefined)?.user;
    const isTyping = (state as { typing?: boolean } | undefined)?.typing;
    if (user?.id && isTyping) typing.push(user.id);
  });
  return typing;
}

export function useCoeditSession(opts: {
  path: string;
  enabled: boolean;
  myUserId: string | null;
  myUserDisplay: string | null;
  onEnd?: () => void;
}): UseCoeditSession {
  const { path, enabled, myUserId, myUserDisplay, onEnd } = opts;

  // The Yjs doc/awareness pair lives for the connection's lifetime — a fresh
  // pair on every (re)connect, so the sync handshake repopulates it from the
  // server's durable state. The cost is that anything typed while
  // disconnected is dropped rather than merged on reconnect; keeping the doc
  // across reconnects would fix that and is now possible (the server never
  // changes document identity), but it isn't what this port does.
  const [doc, setDocInstance] = useState(() => new Y.Doc());
  const [awareness, setAwareness] = useState(() => new Awareness(doc));
  const [connectionId, setConnectionId] = useState(0);
  const [canWrite, setCanWrite] = useState(true);
  const [buffer, setBuffer] = useState("");
  const [participants, setParticipants] = useState<CoeditParticipant[]>([]);
  const [typing, setTyping] = useState<string[]>([]);
  const [peers, setPeers] = useState<CoeditPeer[]>([]);
  const [active, setActive] = useState(false);
  const [joinError, setJoinError] = useState<string | null>(null);
  const [joinErrorRetryable, setJoinErrorRetryable] = useState(true);
  // 0 while connected; otherwise how many reconnect attempts have failed
  // since the connection dropped (1 the moment the drop is noticed). The
  // indicator renders this state with precedence over save errors: during
  // an outage the save machinery's rejections are consequences, and the
  // recovery is already running.
  const [reconnectAttempts, setReconnectAttempts] = useState(0);
  const [retryToken, setRetryToken] = useState(0);
  // Bumped when the server replaces the session document's CRDT lineage (a
  // checkpoint reseed): re-runs the whole session effect, so the doc pair,
  // presence, timers, and status all reset through the one existing cleanup
  // path instead of a second hand-rolled lifecycle inside the loop.
  const [resyncToken, setResyncToken] = useState(0);
  const retryJoin = useCallback(() => {
    setJoinError(null);
    setJoinErrorRetryable(true);
    setRetryToken((t) => t + 1);
  }, []);
  const [saveStatus, setSaveStatus] =
    useState<UseCoeditSession["saveStatus"]>("saved");
  const [saveError, setSaveError] = useState<string | null>(null);
  const saveRetryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The post-reconnect save kick, cancellable on teardown/reconnect.
  const reconnectKick = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveRetries = useRef(0);
  // Counted apart from saveRetries: a timeout and a "not connected" cost two
  // very different amounts of wall-clock, so they can't share a budget.
  const saveTimeouts = useRef(0);
  // Guards against overlapping saves — a retry firing while an autosave is
  // still in flight, or the reverse. Coalescing is safe rather than lossy: a
  // checkpoint commits whatever the document currently holds, not a snapshot
  // taken when it was requested, so the in-flight one already covers the
  // skipped one's content. Without this, two promises settling out of order
  // could leave a false "Couldn't save" sitting on top of a success (the server
  // is fine either way — the per-session advisory lock means repeated attempts
  // still produce a single commit).
  const saveInFlight = useRef(false);
  // Reached through a ref so a queued retry always runs the current closure,
  // not the one captured when the failure happened.
  const checkpointRef = useRef<(() => Promise<void>) | null>(null);

  /** Returns whether a retry was actually armed, so the caller can say so
   * without promising one the budget won't allow. */
  const armSaveRetry = useCallback((timedOut = false): boolean => {
    const used = timedOut ? saveTimeouts : saveRetries;
    const limit = timedOut ? SAVE_TIMEOUT_RETRY_LIMIT : SAVE_RETRY_LIMIT;
    if (used.current >= limit) return false;
    used.current += 1;
    if (saveRetryTimer.current) clearTimeout(saveRetryTimer.current);
    saveRetryTimer.current = setTimeout(() => {
      saveRetryTimer.current = null;
      void checkpointRef.current?.();
    }, SAVE_RETRY_MS);
    return true;
  }, []);

  const sessionId = useRef<number | null>(null);
  // The connection this hook currently owns — svc.ts's per-socket handle.
  // Distinct from sessionId, which is stable across reconnects: save/close must
  // act on *this* socket, never on whichever one happens to hold the session
  // now. Also distinct from the `connectionId` state above, which is a remount
  // counter for the doc/awareness pair.
  const ownedConnection = useRef<number | null>(null);
  const editorRef = useRef<Editor | null>(null);
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const maxIntervalTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const typingIdleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearAutosaveTimers = useCallback(() => {
    if (idleTimer.current) {
      clearTimeout(idleTimer.current);
      idleTimer.current = null;
    }
    if (maxIntervalTimer.current) {
      clearTimeout(maxIntervalTimer.current);
      maxIntervalTimer.current = null;
    }
    if (saveRetryTimer.current) {
      clearTimeout(saveRetryTimer.current);
      saveRetryTimer.current = null;
    }
  }, []);

  const checkpoint = useCallback(async () => {
    if (!canWrite || saveInFlight.current) return;
    const cid = ownedConnection.current;
    if (cid === null) {
      // Mid-reconnect: there is no socket to ask, and this used to return
      // silently — so a save owed right before a drop was simply never made by
      // the client, leaving it to the server's 300s idle scan. Arm the retry
      // instead, which the reconnect will satisfy.
      armSaveRetry();
      return;
    }
    setSaveStatus("saving");
    saveInFlight.current = true;
    try {
      await checkpointSession(cid);
      saveRetries.current = 0;
      saveTimeouts.current = 0;
      setSaveError(null);
      setSaveStatus("saved");
    } catch (e) {
      const reason = e instanceof Error ? e.message : String(e);
      // Logged as well as surfaced: the indicator has room for a short reason,
      // and a stack in the console is what makes an intermittent report
      // actionable.
      console.warn("[coedit] save failed", e);
      const timedOut = reason === CHECKPOINT_TIMED_OUT;
      // A permission failure is the only terminal one — retrying it just
      // repeats the refusal. Everything else means "no socket right now", which
      // the reconnect loop is already fixing.
      const terminal = reason.toLowerCase().includes("forbidden");
      const retrying = !terminal && armSaveRetry(timedOut);
      // A rejection that just restates the outage ("connection closed", "not
      // connected") gets instruction instead of diagnosis: the reconnect
      // loop is already running, a fresh connection re-fires the save
      // (see the kick after a successful join), and the one thing the user
      // must know is that closing the tab is what would lose the edits.
      const connectionish =
        !timedOut &&
        (reason === "connection closed" || reason === "not connected");
      // Reported as "unconfirmed", not "error": see saveStatus. The detail says
      // what happens next rather than what went wrong, and once the budget is
      // spent that is the server's periodic scan, which commits a dirty session
      // without the client's help. Neither string repeats the status label —
      // the indicator prefixes it.
      setSaveError(
        timedOut
          ? retrying
            ? "retrying"
            : "the server will commit it shortly"
          : connectionish
            ? "retries once reconnected"
            : reason,
      );
      setSaveStatus(timedOut ? "unconfirmed" : "error");
    } finally {
      saveInFlight.current = false;
    }
  }, [canWrite, armSaveRetry]);

  checkpointRef.current = checkpoint;

  const runAutoCheckpoint = useCallback(() => {
    clearAutosaveTimers();
    void checkpoint();
  }, [clearAutosaveTimers, checkpoint]);

  const armAutosave = useCallback(() => {
    if (idleTimer.current) clearTimeout(idleTimer.current);
    idleTimer.current = setTimeout(runAutoCheckpoint, AUTOSAVE_IDLE_MS);
    if (!maxIntervalTimer.current) {
      maxIntervalTimer.current = setTimeout(
        runAutoCheckpoint,
        AUTOSAVE_MAX_INTERVAL_MS,
      );
    }
  }, [runAutoCheckpoint]);

  const recomputePresence = useCallback(() => {
    setTyping(deriveTyping(awareness));
    const editor = editorRef.current;
    if (editor) setPeers(derivePeers(editor, awareness));
  }, [awareness]);

  const onEditorReady = useCallback(
    (editor: Editor) => {
      editorRef.current = editor;
      recomputePresence();
      setBuffer(editor.getText());
      editor.on("update", () => {
        setBuffer(editor.getText());
        armAutosave();
        awareness.setLocalStateField("typing", true);
        if (typingIdleTimer.current) clearTimeout(typingIdleTimer.current);
        typingIdleTimer.current = setTimeout(() => {
          awareness.setLocalStateField("typing", false);
        }, TYPING_IDLE_MS);
      });
    },
    [awareness, armAutosave, recomputePresence],
  );

  const setDoc = useCallback((text: string) => {
    const editor = editorRef.current;
    if (!editor) return;
    const paragraphs = text.split(/\n{2,}/).filter((p) => p.trim() !== "");
    editor.commands.setContent(
      paragraphs.length > 0
        ? paragraphs.map((p) => ({
            type: "paragraph",
            content: [{ type: "text", text: p }],
          }))
        : [{ type: "paragraph" }],
    );
  }, []);

  // Connect while enabled; close on disable/path-change/unmount.
  // `retryToken` lets `retryJoin` force a re-run without `enabled`/`path`
  // changing.
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setJoinError(null);
    // Per-connection-lifecycle state: without this, switching pages during
    // an outage carries the old page's "Reconnecting…" onto the new one
    // until its first successful join.
    setReconnectAttempts(0);

    // One doc/awareness pair per effect run — that is, per path — reused across
    // every reconnect within it.
    //
    // NOT per reconnect, which is what this did before. A new `Y.Doc` mints a
    // new Yjs client id; `yCursorPlugin` keys carets by client id; and nothing
    // retires the old one, so every reconnect left a duplicate caret on every
    // peer's screen until y-protocols' 30s `outdatedTimeout` swept it — the
    // "why are there multiple Duo carets" report, and what a caret appearing to
    // jump actually was. Reusing the pair also stops the editor remounting on
    // reconnect (so your own caret stays put) and lets anything typed while
    // disconnected merge on reconnect instead of being discarded.
    //
    // The one exception is a server-signaled resync: the server *replaced* the
    // document's CRDT lineage (a checkpoint divergence it couldn't splice), so
    // this doc can never converge with the session again — syncing the two
    // unions both documents' content into a duplicated page. That case bumps
    // `resyncToken`, re-running this whole effect: the cleanup below tears the
    // pair down and the next run builds a fresh one.
    //
    // Still never the outer `doc`/`awareness` state: this effect re-runs on
    // every path change and React does not reset that state, so reusing it would
    // hand the previous path's populated doc to this path's session and risk it
    // being checkpointed under the wrong page.
    const sessionDoc = new Y.Doc();
    const sessionAwareness = new Awareness(sessionDoc);
    setDocInstance(sessionDoc);
    setAwareness(sessionAwareness);
    editorRef.current = null;
    setBuffer("");
    setConnectionId((n) => n + 1);
    if (myUserId)
      sessionAwareness.setLocalStateField("user", {
        id: myUserId,
        name: myUserDisplay ?? "Anonymous",
      });

    // Closes over this run's own awareness rather than calling the outer
    // `recomputePresence`, whose useCallback identity is pinned to whichever
    // `awareness` state value existed when this effect body started.
    const recomputeForThisAwareness = () => {
      setTyping(deriveTyping(sessionAwareness));
      const editor = editorRef.current;
      if (editor) setPeers(derivePeers(editor, sessionAwareness));
    };
    sessionAwareness.on("change", recomputeForThisAwareness);

    void (async () => {
      let failedAttempts = 0;
      // The lineage generation this run's pair has synced against, from the
      // last successful join; null until the first one (a fresh doc belongs
      // to no generation, so its first join can never mismatch). A reconnect
      // that lands on a *different* generation means the server reseeded
      // while we were away — the pair must be discarded before any further
      // sync, same as an explicit resync frame. Both cases end this run via
      // `resyncToken`; the effect re-run starts over with a fresh pair, which
      // is also why a lineage disagreement can't loop: a fresh pair passes
      // `null` and always joins cleanly.
      let docLineage: number | null = null;
      const resync = (why: string) => {
        // Whatever this pair held beyond its last relayed update is
        // unrecoverable by design — a replaced lineage can't be merged
        // without duplicating the page — so leave a trace for support.
        console.warn(
          `[coedit] server replaced the document lineage (${why}); ` +
            "rebuilding the editor — unsynced local edits are discarded",
        );
        setResyncToken((t) => t + 1);
      };
      while (!cancelled) {
        let snap;
        try {
          snap = await connectSession(
            path,
            sessionDoc,
            sessionAwareness,
            docLineage,
            (p) => {
              if (!cancelled) setParticipants(p);
            },
          );
        } catch (e) {
          if (cancelled) return;
          // A 422 is svc.ts's join_error frame — a real, distinguishable
          // reason retrying won't fix (e.g. a codec-unsupported page). Only
          // that ends the loop. Every other failure here — network error, a
          // dropped connection, the few seconds a backend deploy is
          // unreachable — used to end it too, stranding the tab behind a
          // Retry button for an outage that had already passed; now it backs
          // off and tries again, and the indicator says so.
          if (e instanceof ApiError && e.status === 422) {
            setJoinErrorRetryable(false);
            setJoinError(
              e instanceof Error
                ? e.message
                : "Failed to join the editing session.",
            );
            return;
          }
          failedAttempts += 1;
          setReconnectAttempts(failedAttempts);
          await new Promise((r) =>
            setTimeout(
              r,
              Math.min(
                RECONNECT_BACKOFF_MAX_MS,
                STREAM_RECONNECT_MS * 2 ** Math.min(failedAttempts - 1, 4),
              ),
            ),
          );
          continue;
        }
        if (cancelled) {
          closeSession(snap.connectionId);
          return;
        }
        if (snap.staleLineage) {
          // The server reseeded the document while we were disconnected: this
          // pair holds a replaced lineage (svc has already suppressed its
          // sync). Drop the connection and start the effect over.
          closeSession(snap.connectionId);
          if (!cancelled) resync("reconnected onto a newer generation");
          return;
        }
        // `?? docLineage`: a join answered by a backend that predates the
        // field (mid rolling deploy) must not erase a generation this pair
        // already learned — nulling it would disarm the mismatch check on
        // the next reconnect and let a reseeded lineage merge with this one.
        docLineage = snap.lineage ?? docLineage;
        failedAttempts = 0;
        setReconnectAttempts(0);
        setJoinError(null);

        sessionId.current = snap.session_id;
        ownedConnection.current = snap.connectionId;
        // Both save budgets are per-socket: they exist to stop retrying at a
        // socket that isn't answering, and this is a different one. Without
        // resetting, a spent budget outlived the connection whose failures
        // spent it, so a timeout on a healthy new socket skipped the retry that
        // would have recovered it and waited for the server's scan instead.
        saveRetries.current = 0;
        saveTimeouts.current = 0;
        setCanWrite(snap.can_write);
        setParticipants(snap.participants);
        setActive(true);
        // A save that exhausted its retries during the outage stays failed
        // until something re-asks; make the reconnect be that something. A
        // clean session makes this a no-op server-side, so the cost is one
        // frame per reconnect. Two subtleties, both observed live:
        // gate on the join's own can_write (the checkpoint closure still
        // holds the previous render's value, so a read-only viewer's kick
        // would earn a permanent "forbidden" error), and wait one autosave
        // interval — edits typed while offline reach the server in the sync
        // exchange *after* the join resolves, so an immediate kick finds a
        // clean session and covers nothing.
        if (reconnectKick.current) clearTimeout(reconnectKick.current);
        if (snap.can_write) {
          reconnectKick.current = setTimeout(() => {
            reconnectKick.current = null;
            void checkpointRef.current?.();
          }, AUTOSAVE_IDLE_MS);
        }

        const { expected, resync: resyncClose } = await snap.closed;
        if (ownedConnection.current === snap.connectionId)
          ownedConnection.current = null;
        if (cancelled || expected) return;
        if (resyncClose) {
          // The server reseeded this session's document mid-connection.
          resync("resync_required");
          return;
        }
        // The connection dropped: the whole gap until the next successful
        // join renders as "Reconnecting…", not as a save failure — the save
        // machinery's rejections during this window are consequences of the
        // drop, and the recovery is already in motion.
        setReconnectAttempts((n) => Math.max(n, 1));
        await new Promise((r) => setTimeout(r, STREAM_RECONNECT_MS));
      }
    })();

    return () => {
      cancelled = true;
      clearAutosaveTimers();
      if (reconnectKick.current) {
        clearTimeout(reconnectKick.current);
        reconnectKick.current = null;
      }
      if (typingIdleTimer.current) clearTimeout(typingIdleTimer.current);
      setActive(false);
      setPeers([]);
      setTyping([]);
      sessionAwareness.off("change", recomputeForThisAwareness);
      // Captured now, and acted on below *by this handle*: the effect body for
      // the next path/retry starts before this async teardown finishes, and it
      // joins the same session id. Closing "the session" at that point would
      // close the new socket instead of this one.
      const cid = ownedConnection.current;
      sessionId.current = null;
      ownedConnection.current = null;
      onEnd?.();
      if (cid === null) {
        sessionAwareness.destroy();
        return;
      }
      void (async () => {
        if (canWrite) {
          try {
            await checkpointSession(cid);
          } catch {
            // The server-side close-triggered forced commit is the backstop.
          }
        }
        // Before closing, not after: `destroy()` sets our local state to null,
        // which emits an awareness update the still-open socket relays, so peers
        // drop this caret immediately instead of waiting out the 30s timeout. It
        // also clears the Awareness heartbeat interval. The Doc has no timers
        // and is left to GC, since the editor may still be tearing down around
        // it.
        sessionAwareness.destroy();
        closeSession(cid);
      })();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, path, retryToken, resyncToken]);

  return {
    active,
    connectionId,
    doc,
    awareness,
    canWrite,
    buffer,
    participants,
    typing,
    peers,
    joinError,
    joinErrorRetryable,
    retryJoin,
    reconnectAttempts,
    saveStatus,
    saveError,
    onEditorReady,
    setDoc,
  };
}
