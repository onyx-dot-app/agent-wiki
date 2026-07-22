"use client";

import { useRef, useState } from "react";
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
import {
  detokenizeMentions,
  parseBody,
  tokenizeMentions,
} from "@/lib/commentMentions";
import { parseTs, relativeTime } from "@/lib/time";
import type { CommentView } from "@/types";

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
        emphasized ? "text-(--text-04)" : "text-(--text-03)"
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
      if (!ok) return;
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  // Hover swaps the timestamp+badge for the action cluster. Both live in the
  // same grid cell (sized by the larger) and toggle visibility, never display,
  // so the card's geometry is identical in both states and hover cannot shift
  // the cards below. An open menu pins the cluster so it can't vanish under
  // the popover.
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
          <Popover.Content width="fit" align="end">
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
    <div className="group/comment flex w-full flex-col">
      <Section
        flexDirection="row"
        justifyContent="start"
        alignItems="center"
        height="fit"
        gap={0.25}
        className="px-1 pt-1"
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
  quotedText,
  disabled,
  onSubmit,
  onCancel,
}: {
  selfName: string;
  quotedText: string;
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
        className="px-1 pt-1"
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
      {/* The selected text the comment anchors to. The doc highlight is the
          primary cue, this echoes it inside the composer. */}
      <div className="truncate px-2 text-[12px] leading-4 text-(--text-03)">
        {quotedText}
      </div>
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
