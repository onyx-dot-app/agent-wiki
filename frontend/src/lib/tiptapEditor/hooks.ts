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
import {
  checkpointSession,
  closeSession,
  connectSession,
  disconnectForResync,
  type CoeditParticipant,
} from "@/lib/tiptapEditor/svc";

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
   * without a `key` forcing a full remount, a resync's fresh `Y.Doc` would
   * never actually reach the editor. Bumps on every fresh doc/awareness
   * pair — a resync-triggered reconnect can land back on the *same*
   * session_id (the session isn't closed, just its content changed), so
   * that id alone can't be used as the key the way the old hook's
   * `session.id` was. */
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

  // The Yjs doc/awareness pair lives for the connection's lifetime — a
  // fresh pair on every (re)connect, since a resync (checkpoint merge,
  // live-rebase) means the server's doc identity changed and stale local
  // state can't be reconciled onto it incrementally (see ResyncFrame).
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
  const [retryToken, setRetryToken] = useState(0);
  const retryJoin = useCallback(() => {
    setJoinError(null);
    setRetryToken((t) => t + 1);
  }, []);
  const [saveStatus, setSaveStatus] =
    useState<UseCoeditSession["saveStatus"]>("saved");

  const sessionId = useRef<number | null>(null);
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
    const sid = sessionId.current;
    if (sid === null || !canWrite) return;
    setSaveStatus("saving");
    try {
      await checkpointSession(sid);
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
      let firstConnect = true;
      while (!cancelled) {
        // Fresh doc/awareness per connection attempt — see the field
        // comment on `doc` above.
        const freshDoc = firstConnect ? doc : new Y.Doc();
        const freshAwareness = firstConnect
          ? awareness
          : new Awareness(freshDoc);
        if (!firstConnect) {
          setDocInstance(freshDoc);
          setAwareness(freshAwareness);
          editorRef.current = null;
          setBuffer("");
        }
        setConnectionId((n) => n + 1);

        let snap;
        try {
          snap = await connectSession(
            path,
            freshDoc,
            freshAwareness,
            (p) => {
              if (!cancelled) setParticipants(p);
            },
            () => {
              // Server replaced the doc wholesale — this connection's
              // incremental Yjs state is no longer valid. Tear it down as
              // an *unexpected* close (not closeSession) so the loop below
              // reconnects after its usual backoff instead of stopping.
              // sessionId.current is set (a few lines below) before any
              // frame that could plausibly trigger this arrives — the
              // server can't send `resync` before its own `joined`.
              if (!cancelled && sessionId.current !== null) {
                disconnectForResync(sessionId.current);
              }
            },
          );
        } catch (e) {
          if (!cancelled) {
            setJoinError(
              e instanceof Error
                ? e.message
                : "Failed to join the editing session.",
            );
          }
          return;
        }
        if (cancelled) {
          closeSession(snap.session_id);
          return;
        }

        sessionId.current = snap.session_id;
        setCanWrite(snap.can_write);
        setParticipants(snap.participants);
        setActive(true);
        if (myUserId)
          freshAwareness.setLocalStateField("user", {
            id: myUserId,
            name: myUserDisplay ?? "Anonymous",
          });
        freshAwareness.on("change", recomputePresence);
        firstConnect = false;

        const { expected } = await snap.closed;
        freshAwareness.off("change", recomputePresence);
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
      const sid = sessionId.current;
      sessionId.current = null;
      onEnd?.();
      if (sid === null) return;
      void (async () => {
        if (canWrite) {
          try {
            await checkpointSession(sid);
          } catch {
            // The server-side close-triggered forced commit is the backstop.
          }
        }
        closeSession(sid);
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
    retryJoin,
    saveStatus,
    onEditorReady,
    setDoc,
  };
}
