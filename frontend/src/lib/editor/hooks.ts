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
 * pass them to `<TiptapEditor doc={coedit.doc} awareness={coedit.awareness}
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
import {
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

export interface CoeditPeer {
  user_id: string;
  user_display: string;
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
   * `<TiptapEditor key={coedit.connectionId}>`. Required, not cosmetic:
   * Tiptap's `useEditor` defaults to building the editor once and never
   * rebuilding it from new `doc`/`awareness` props (confirmed against the
   * installed `@tiptap/react` — `deps = []` unless the caller opts in), so
   * without a `key` forcing a full remount, a reconnect's fresh `Y.Doc`
   * would never actually reach the editor. Bumps on every fresh
   * doc/awareness pair — a reconnect lands back on the *same* session_id
   * (the session outlives any one socket), so that id alone can't be used as
   * the key the way the old hook's `session.id` was. */
  connectionId: number;
  /** This connection's Yjs doc — pass to `<TiptapEditor doc={...}>`. A
   * fresh instance every time `connectionId` bumps. */
  doc: Y.Doc;
  /** This connection's Awareness — pass to `<TiptapEditor awareness={...}>`. */
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
  /** Set when the join handshake itself fails (network error, 4xx/5xx,
   * expired auth) — `active` stays false forever unless the caller shows
   * this and offers `retryJoin`. A drop *after* a successful join doesn't
   * set this (the session is already usable; the reconnect loop heals it). */
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
  /** Autosave state, for a "Saving…/Saved/Couldn't save" indicator. */
  saveStatus: "saved" | "saving" | "error";
  /** Wire the underlying Tiptap `Editor` instance once it mounts — needed
   * to resolve peer cursor positions and to drive `setDoc`. Pass directly
   * as `<TiptapEditor onEditorReady={coedit.onEditorReady}>`. */
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

function newClientId(): string {
  return crypto.randomUUID();
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
      state as { user?: { id?: string; name?: string } } | undefined
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
      user_id: user.id,
      user_display: user.name ?? "Anonymous",
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
  const [retryToken, setRetryToken] = useState(0);
  const retryJoin = useCallback(() => {
    setJoinError(null);
    setJoinErrorRetryable(true);
    setRetryToken((t) => t + 1);
  }, []);
  const [saveStatus, setSaveStatus] =
    useState<UseCoeditSession["saveStatus"]>("saved");

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
  }, []);

  const checkpoint = useCallback(async () => {
    const cid = ownedConnection.current;
    if (cid === null || !canWrite) return;
    setSaveStatus("saving");
    try {
      await checkpointSession(cid);
      setSaveStatus("saved");
    } catch {
      setSaveStatus("error");
    }
  }, [canWrite]);

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

    void (async () => {
      while (!cancelled) {
        // Fresh doc/awareness every iteration, including the first — never
        // reuse the outer `doc`/`awareness` state: this effect re-runs on
        // every path change (deps: [enabled, path, retryToken]), and since
        // `doc`/`awareness` state isn't reset by React on that re-run, a
        // "first connect reuses the existing state" shortcut here would
        // hand the *previous* path's populated doc to this path's brand
        // new session — syncing its content in and risking it getting
        // checkpointed under the wrong page.
        const freshDoc = new Y.Doc();
        const freshAwareness = new Awareness(freshDoc);
        setDocInstance(freshDoc);
        setAwareness(freshAwareness);
        editorRef.current = null;
        setBuffer("");
        setConnectionId((n) => n + 1);

        let snap;
        try {
          snap = await connectSession(path, freshDoc, freshAwareness, (p) => {
            if (!cancelled) setParticipants(p);
          });
        } catch (e) {
          if (!cancelled) {
            // A 422 is svc.ts's join_error frame — a real, distinguishable
            // reason retrying won't fix (e.g. a codec-unsupported page),
            // unlike every other failure here (network error, expired
            // auth, a plain dropped connection), which really is worth
            // retrying.
            setJoinErrorRetryable(!(e instanceof ApiError && e.status === 422));
            setJoinError(
              e instanceof Error
                ? e.message
                : "Failed to join the editing session.",
            );
          }
          return;
        }
        if (cancelled) {
          closeSession(snap.connectionId);
          return;
        }

        sessionId.current = snap.session_id;
        ownedConnection.current = snap.connectionId;
        setCanWrite(snap.can_write);
        setParticipants(snap.participants);
        setActive(true);
        if (myUserId)
          freshAwareness.setLocalStateField("user", {
            id: myUserId,
            name: myUserDisplay ?? "Anonymous",
          });
        // Not the outer recomputePresence: this whole while loop runs
        // inside one long-lived effect invocation (the effect's own deps,
        // [enabled, path, retryToken], don't include `awareness`), so
        // across reconnects within a single run, recomputePresence's
        // useCallback closure stays pinned to whichever `awareness` state
        // value existed when the effect body started — reading it here
        // would derive presence from a stale, already-disconnected
        // Awareness instance after setAwareness(freshAwareness) above.
        // Closing over freshAwareness directly sidesteps the staleness
        // instead of chasing it.
        const recomputeForThisAwareness = () => {
          setTyping(deriveTyping(freshAwareness));
          const editor = editorRef.current;
          if (editor) setPeers(derivePeers(editor, freshAwareness));
        };
        freshAwareness.on("change", recomputeForThisAwareness);

        const { expected } = await snap.closed;
        if (ownedConnection.current === snap.connectionId)
          ownedConnection.current = null;
        freshAwareness.off("change", recomputeForThisAwareness);
        if (cancelled || expected) return;
        await new Promise((r) => setTimeout(r, STREAM_RECONNECT_MS));
      }
    })();

    return () => {
      cancelled = true;
      clearAutosaveTimers();
      if (typingIdleTimer.current) clearTimeout(typingIdleTimer.current);
      setActive(false);
      setPeers([]);
      setTyping([]);
      // Captured now, and acted on below *by this handle*: the effect body for
      // the next path/retry starts before this async teardown finishes, and it
      // joins the same session id. Closing "the session" at that point would
      // close the new socket instead of this one.
      const cid = ownedConnection.current;
      sessionId.current = null;
      ownedConnection.current = null;
      onEnd?.();
      if (cid === null) return;
      void (async () => {
        if (canWrite) {
          try {
            await checkpointSession(cid);
          } catch {
            // The server-side close-triggered forced commit is the backstop.
          }
        }
        closeSession(cid);
      })();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, path, retryToken]);

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
    saveStatus,
    onEditorReady,
    setDoc,
  };
}
