"use client";

import { useEffect, useRef, useState } from "react";
import {
  Button,
  Divider,
  LineItemButton,
  Popover,
  Text,
} from "@onyx-ai/opal/components";
import {
  SvgArrowUp,
  SvgCheck,
  SvgCheckSquare,
  SvgEdit,
  SvgLink,
  SvgMoreHorizontal,
  SvgTrash,
  SvgX,
} from "@onyx-ai/opal/icons";
import { Section } from "@onyx-ai/opal/layouts";

import { MentionTextarea } from "@/components/wiki/MentionTextarea";
import { toast } from "@/hooks/useToast";
import {
  deleteComment,
  editComment,
  reopenThread,
  replyToComment,
  resolveThread,
} from "@/lib/comments";
import {
  detokenizeMentions,
  parseBody,
  tokenizeMentions,
} from "@/lib/commentMentions";
import { parseTs, relativeTime } from "@/lib/time";
import { shareableWikiUrl } from "@/lib/wikiHref";
import type { CommentThreadView, CommentView } from "@/types";

// A thread reads as new while its latest message is inside this window
// (comments carry no per-user read state, mirroring the events feed).
const NEW_COMMENT_MS = 24 * 60 * 60 * 1000;

export function isNewComment(createdAt: string): boolean {
  return Date.now() - parseTs(createdAt).getTime() < NEW_COMMENT_MS;
}

/** 20px initial avatar, the comment cards' author mark (mock 669:264296). */
function CommentAvatar({ name }: { name: string }) {
  return (
    <span className="flex size-6 shrink-0 items-center justify-center p-[2px]">
      <span
        aria-hidden
        className="box-border flex size-5 items-center justify-center overflow-hidden rounded-full border border-(--border-01) bg-(--background-neutral-inverted-00) text-xs font-semibold text-(--text-inverted-05)"
      >
        {(name.charAt(0) || "?").toUpperCase()}
      </span>
    </span>
  );
}

/** Unread/read/resolved marker in the message's 16px badge slot. */
function CommentBadge({
  unread,
  resolved,
}: {
  unread: boolean;
  resolved: boolean;
}) {
  return (
    <span className="flex size-4 shrink-0 items-center justify-center">
      {resolved ? (
        <SvgCheckSquare size={16} className="text-(--text-03)" />
      ) : unread ? (
        <span className="size-[6px] rounded-full bg-(--status-info-05)" />
      ) : (
        <span className="size-2 rounded-full border border-(--border-02)" />
      )}
    </span>
  );
}

/** Stored bodies keep mentions as tokens, rendered here as chips. */
function CommentBody({
  body,
  emphasized,
}: {
  body: string;
  emphasized: boolean;
}) {
  return (
    <p
      className={`text-sm leading-5 font-medium ${
        emphasized
          ? "text-(--text-04)"
          : "text-(--text-03) group-hover/comment:text-(--text-04)"
      }`}
    >
      {parseBody(body).map((seg, i) =>
        seg.kind === "mention" ? (
          <span
            key={i}
            className="rounded-(--radius-04) bg-(--background-tint-03) px-[3px] text-(--text-05)"
          >
            @{seg.name}
          </span>
        ) : (
          <span key={i}>{seg.text}</span>
        ),
      )}
    </p>
  );
}

export interface MessageActions {
  onResolve?: () => void;
  onReopen?: () => void;
  onCopyLink: () => Promise<boolean>;
  onEdit: (body: string) => Promise<boolean>;
  onDelete: () => void;
}

/** One message row: title line (avatar, author, time or hover actions) over
 *  the body line (mocks 669:264296 collapsed, 778:262971 expanded). Per the
 *  mock's dev annotation the More menu (Copy Link / Edit / Delete) belongs
 *  to the commenter, everyone else gets a bare link button. */
export function CommentMessage({
  comment,
  authorName,
  isRoot,
  resolved,
  unread,
  emphasized,
  canModify,
  busy,
  actions,
}: {
  comment: CommentView;
  authorName: string;
  isRoot: boolean;
  resolved: boolean;
  unread: boolean;
  /** Expanded/hovered threads render the body in text-04 (mock). */
  emphasized: boolean;
  canModify: boolean;
  busy: boolean;
  actions: MessageActions;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editBody, setEditBody] = useState("");
  const [copied, setCopied] = useState(false);
  const editMentions = useRef<Record<string, string>>({});

  const startEdit = () => {
    setMenuOpen(false);
    // Edit in the readable display form, (de)tokenizing at the boundary so
    // the textarea shows "@Name" while storage keeps the user id.
    const d = detokenizeMentions(comment.body);
    setEditBody(d.text);
    editMentions.current = d.map;
    setEditing(true);
  };

  const copyLink = () => {
    void actions.onCopyLink().then((ok) => {
      // A silent failure reads as success (the user pastes stale clipboard),
      // so the miss must surface.
      if (!ok) {
        toast.error("Couldn't copy the link.");
        return;
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  // Timestamp+badge and the action cluster share one grid cell and toggle
  // visibility, never display, so hover cannot change card geometry. An
  // open menu pins the cluster visible under the popover.
  const actionCluster = (
    <span
      className={`col-start-1 row-start-1 flex items-center justify-self-end ${menuOpen ? "" : "invisible group-hover/comment:visible"}`}
    >
      {isRoot && (
        <Button
          icon={SvgCheckSquare}
          size="md"
          prominence="tertiary"
          tooltip={resolved ? "Reopen" : "Resolve"}
          disabled={busy}
          onClick={resolved ? actions.onReopen : actions.onResolve}
        />
      )}
      {canModify ? (
        <Popover open={menuOpen} onOpenChange={setMenuOpen}>
          <Popover.Trigger asChild>
            <span className="inline-flex">
              <Button
                icon={SvgMoreHorizontal}
                size="md"
                prominence="tertiary"
                tooltip="More"
              />
            </span>
          </Popover.Trigger>
          {/* The mock opens the menu above the card, right edge hanging 4px
              past it (670:266803). alignOffset is the horizontal overhang,
              sideOffset the vertical gap. */}
          <Popover.Content
            width="fit"
            side="top"
            align="end"
            sideOffset={4}
            alignOffset={-4}
          >
            <Popover.Menu>
              <LineItemButton
                title={copied ? "Link copied" : "Copy Link"}
                icon={copied ? SvgCheck : SvgLink}
                sizePreset="main-ui"
                variant="section"
                onClick={copyLink}
              />
              <LineItemButton
                title="Edit"
                icon={SvgEdit}
                sizePreset="main-ui"
                variant="section"
                onClick={startEdit}
              />
              <Divider paddingParallel="sm" paddingPerpendicular="xs" />
              <LineItemButton
                title="Delete"
                color="danger"
                icon={SvgTrash}
                sizePreset="main-ui"
                variant="section"
                onClick={() => {
                  setMenuOpen(false);
                  actions.onDelete();
                }}
              />
            </Popover.Menu>
          </Popover.Content>
        </Popover>
      ) : (
        <Button
          icon={copied ? SvgCheck : SvgLink}
          size="md"
          prominence="tertiary"
          tooltip={copied ? "Link copied" : "Copy link"}
          onClick={copyLink}
        />
      )}
    </span>
  );

  return (
    <div className="flex w-full flex-col">
      <Section
        flexDirection="row"
        justifyContent="start"
        alignItems="center"
        width="auto"
        height="fit"
        gap={0.25}
        // width="auto", not the default w-full: the flex-column parent then
        // stretches the row between these margins. With w-full the margins
        // push the row past the card's edge and overflow-clip eats the More
        // button (Section's inline padding style rules out padding classes).
        // Wider right inset than left so the button's hover pill clears the
        // rounded corner instead of hugging it.
        className="mt-1 mr-2 ml-1"
      >
        <CommentAvatar name={authorName} />
        <span className="min-w-0 flex-1">
          <Text font="main-ui-action" color="text-04" nowrap maxLines={1}>
            {authorName}
          </Text>
        </span>
        <span className="grid shrink-0">
          <span
            className={`col-start-1 row-start-1 flex items-center gap-1 justify-self-end p-[2px] ${menuOpen ? "invisible" : "group-hover/comment:invisible"}`}
          >
            <span
              className={`px-[2px] text-[12px] leading-4 whitespace-nowrap ${
                unread && !resolved
                  ? "text-(--status-text-info-05)"
                  : "text-(--text-03)"
              }`}
            >
              {relativeTime(comment.created_at, "long")}
            </span>
            <CommentBadge unread={unread} resolved={resolved} />
          </span>
          {actionCluster}
        </span>
      </Section>
      <div className="px-2 pt-1 pb-2">
        {editing ? (
          <CommentInput
            placeholder="Edit comment…"
            value={editBody}
            onChange={setEditBody}
            onPickMention={(d, id) => {
              editMentions.current[d] = id;
            }}
            disabled={busy}
            submitTooltip="Save"
            onSubmit={async () => {
              const ok = await actions.onEdit(
                tokenizeMentions(editBody.trim(), editMentions.current),
              );
              if (ok) setEditing(false);
            }}
          />
        ) : (
          <CommentBody body={comment.body} emphasized={emphasized} />
        )}
      </div>
    </div>
  );
}

/** The mock's comment input (778:262971 reply, 566:19918 composer): white
 *  bordered field with an inline arrow-up send. Wraps MentionTextarea so
 *  @-mentions keep working inside the new chrome (.comment-input styles the
 *  textarea, globals.css). */
export function CommentInput({
  placeholder,
  value,
  onChange,
  onPickMention,
  onSubmit,
  disabled,
  autoFocus,
  submitTooltip,
  prominentSend,
}: {
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  onPickMention: (display: string, userId: string) => void;
  onSubmit: () => void | Promise<void>;
  disabled: boolean;
  autoFocus?: boolean;
  submitTooltip: string;
  /** Composer sends are the filled 28px button (mock 566:19918). Replies
   *  and edits get the 24px transparent arrow (mocks 778:262971, 670:266803). */
  prominentSend?: boolean;
}) {
  const canSend = !disabled && value.trim().length > 0;
  return (
    <div
      className={`comment-input relative w-full rounded-(--radius-08) border border-(--border-02) bg-(--background-neutral-00) p-[5px] ${prominentSend ? "pr-9" : "pr-8"} focus-within:border-(--border-05) focus-within:shadow-[0_0_0_2px_var(--background-tint-04)]`}
    >
      <MentionTextarea
        placeholder={placeholder}
        value={value}
        autoFocus={autoFocus}
        onChange={onChange}
        onPickMention={onPickMention}
        onSubmit={() => {
          if (canSend) void onSubmit();
        }}
      />
      <span className="absolute right-1 bottom-1">
        {prominentSend ? (
          <Button
            icon={SvgArrowUp}
            variant="action"
            size="md"
            tooltip={submitTooltip}
            disabled={!canSend}
            onClick={() => void onSubmit()}
          />
        ) : (
          <Button
            icon={SvgArrowUp}
            size="sm"
            prominence="tertiary"
            tooltip={submitTooltip}
            disabled={!canSend}
            onClick={() => void onSubmit()}
          />
        )}
      </span>
    </div>
  );
}

/** New-comment composer (mock 566:19918): self avatar + name + close over a
 *  focused input with the black send button. Chromeless wrapper per the
 *  mock (no card fill or shadow). */
export function NewCommentComposer({
  selfName,
  disabled,
  onSubmit,
  onCancel,
}: {
  selfName: string;
  disabled: boolean;
  onSubmit: (body: string) => void;
  onCancel: () => void;
}) {
  const [body, setBody] = useState("");
  const mentions = useRef<Record<string, string>>({});
  return (
    <Section
      justifyContent="start"
      alignItems="stretch"
      height="fit"
      gap={0.25}
      className="rounded-(--radius-08)"
    >
      <Section
        flexDirection="row"
        justifyContent="start"
        alignItems="center"
        height="fit"
        gap={0.25}
        className="mx-1 mt-1"
      >
        <CommentAvatar name={selfName} />
        <span className="min-w-0 flex-1">
          <Text font="main-ui-action" color="text-04" nowrap maxLines={1}>
            {selfName}
          </Text>
        </span>
        <Button
          icon={SvgX}
          size="md"
          prominence="tertiary"
          tooltip="Close"
          onClick={onCancel}
        />
      </Section>
      <div className="px-1 pb-1">
        <CommentInput
          placeholder="Add a comment…"
          value={body}
          onChange={setBody}
          onPickMention={(d, id) => {
            mentions.current[d] = id;
          }}
          disabled={disabled}
          autoFocus
          submitTooltip="Add comment"
          prominentSend
          onSubmit={() =>
            onSubmit(tokenizeMentions(body.trim(), mentions.current))
          }
        />
      </div>
    </Section>
  );
}

function authorLabel(
  authorUserId: string | null,
  authorDisplay: string | null,
  selfId: string | undefined,
): string {
  if (authorUserId && authorUserId === selfId) return "You";
  return authorDisplay ?? "User";
}

/** Deep-link to a specific thread: the page route reads `?comment=<id>`, opens
 * the panel, and scrolls to the thread's anchored span. Uses the durable
 * id-based URL so the link survives a page rename/move. */
function commentLink(path: string, rootId: string): Promise<string> {
  return shareableWikiUrl(path, `comment=${rootId}`);
}

/** One thread card (mocks 1856 list, 669 anchored): collapsed shows the
 *  root, expanded the whole conversation with a reply input (mock 778).
 *  State is fill + badge, anchored cards round at 12, list at 8. */
export function ThreadCard({
  thread,
  path,
  selfId,
  isAdmin,
  busy,
  active,
  anchored,
  onActivate,
  onDeactivate,
  onHoverChange,
  run,
}: {
  thread: CommentThreadView;
  path: string;
  selfId: string | undefined;
  isAdmin: boolean;
  busy: boolean;
  active: boolean;
  anchored?: boolean;
  onActivate: () => void;
  /** Called when a pointer lands outside the expanded card, collapsing it. */
  onDeactivate?: () => void;
  onHoverChange?: (hovering: boolean) => void;
  run: (fn: () => Promise<unknown>) => Promise<boolean>;
}) {
  const { root } = thread;
  const resolved = root.status === "resolved";
  const conversation = [root, ...thread.replies];
  const latest = conversation[conversation.length - 1]!;
  const unread = !resolved && isNewComment(latest.created_at);

  const [replyBody, setReplyBody] = useState("");
  const [replyMentions] = useState<Record<string, string>>({});
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Clicking off an expanded card collapses it. Portaled popover content
  // (the More menu) counts as inside, a menu click must not collapse the
  // card it acts on.
  useEffect(() => {
    if (!active || !onDeactivate) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Element | null;
      if (!t) return;
      if (rootRef.current?.contains(t)) return;
      if (t.closest?.("[data-radix-popper-content-wrapper]")) return;
      onDeactivate();
    };
    // Capture phase: the editor stops pointer events from bubbling.
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [active, onDeactivate]);

  // Active threads expand to the full conversation, collapsed cards show
  // only the root message (mock 1856 stacks both forms).
  const expanded = active;
  const shown = expanded ? conversation : [root];

  const bg = resolved
    ? "bg-(--background-tint-02)"
    : unread || expanded
      ? "bg-(--background-tint-00)"
      : "bg-(--background-tint-01)";
  const shadow = expanded
    ? "shadow-(--shadow-box-01)"
    : "shadow-(--shadow-box-00) hover:shadow-(--shadow-box-01) hover:bg-(--background-tint-00)";

  const messageActions = (c: CommentView) => ({
    onResolve: () => void run(() => resolveThread(root.id)),
    onReopen: () => void run(() => reopenThread(root.id)),
    onCopyLink: async () => {
      // Durable id-based deep-link (survives rename/move). A transient
      // id-resolve failure skips the copy rather than handing over a
      // fragile path link.
      try {
        const url = await commentLink(path, root.id);
        await navigator.clipboard.writeText(url);
        return true;
      } catch {
        return false;
      }
    },
    onEdit: (body: string) => run(() => editComment(c.id, body)),
    onDelete: () => void run(() => deleteComment(c.id)),
  });

  return (
    <div
      ref={rootRef}
      className="flex w-full shrink-0 flex-col"
      data-thread-id={root.id}
    >
      {/* raw-ok: the card is a selectable region hosting nested buttons and inputs, which a native button cannot contain */}
      <div
        role="button"
        tabIndex={0}
        onClick={onActivate}
        onMouseEnter={() => onHoverChange?.(true)}
        onMouseLeave={() => onHoverChange?.(false)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && e.target === e.currentTarget) {
            e.preventDefault();
            onActivate();
          }
        }}
        className={`group/comment flex w-full cursor-pointer flex-col overflow-clip text-left ${anchored ? "rounded-(--radius-12)" : "rounded-(--radius-08)"} ${bg} ${shadow}`}
      >
        {root.status === "orphaned" && (
          <div className="px-2 pt-1 text-[12px] leading-4 text-(--text-03)">
            Original content deleted
          </div>
        )}
        {shown.map((c) => (
          <CommentMessage
            key={c.id}
            comment={c}
            authorName={authorLabel(c.author_user_id, c.author_display, selfId)}
            isRoot={c.id === root.id}
            resolved={resolved}
            unread={unread && !expanded}
            emphasized={expanded}
            canModify={isAdmin || c.author_user_id === selfId}
            busy={busy}
            actions={messageActions(c)}
          />
        ))}
        {!expanded && thread.replies.length > 0 && (
          <span className="px-[10px] pb-2 text-[12px] leading-4 text-(--text-03)">
            {thread.replies.length}{" "}
            {thread.replies.length === 1 ? "reply" : "replies"}
          </span>
        )}
      </div>
      {expanded && !resolved && (
        <div className="pt-1 pb-3">
          <CommentInput
            placeholder="Reply…"
            value={replyBody}
            onChange={setReplyBody}
            onPickMention={(d, id) => {
              replyMentions[d] = id;
            }}
            disabled={busy}
            submitTooltip="Send reply"
            onSubmit={async () => {
              // Clear only on success so a failed reply isn't lost.
              const ok = await run(() =>
                replyToComment(
                  root.id,
                  tokenizeMentions(replyBody.trim(), replyMentions),
                ),
              );
              if (ok) setReplyBody("");
            }}
          />
        </div>
      )}
    </div>
  );
}
