import { apiFetch } from "./api";
import type { CommentThreadView, CommentView } from "@/types";

/** List a page's comments, grouped into threads (root + replies). */
export async function listComments(path: string): Promise<CommentThreadView[]> {
  const r = await apiFetch<{ threads: CommentThreadView[] }>(
    `/comments?path=${encodeURIComponent(path)}`,
  );
  return r.threads;
}

export interface CreateCommentInput {
  path: string;
  /** The commit the offsets were computed against (the version the client read). */
  anchorSha: string;
  startOffset: number;
  endOffset: number;
  quotedText: string;
  body: string;
}

/** Start an inline comment thread anchored to a text range. */
export function createComment(input: CreateCommentInput): Promise<CommentView> {
  return apiFetch<CommentView>("/comments", {
    method: "POST",
    body: JSON.stringify({
      path: input.path,
      anchor_sha: input.anchorSha,
      start_offset: input.startOffset,
      end_offset: input.endOffset,
      quoted_text: input.quotedText,
      body: input.body,
    }),
  });
}

/** Reply to a comment (root or another reply). */
export function replyToComment(
  commentId: string,
  body: string,
): Promise<CommentView> {
  return apiFetch<CommentView>(`/comments/${commentId}/replies`, {
    method: "POST",
    body: JSON.stringify({ body }),
  });
}

export function editComment(
  commentId: string,
  body: string,
): Promise<CommentView> {
  return apiFetch<CommentView>(`/comments/${commentId}`, {
    method: "PATCH",
    body: JSON.stringify({ body }),
  });
}

export function resolveThread(commentId: string): Promise<CommentView> {
  return apiFetch<CommentView>(`/comments/${commentId}/resolve`, {
    method: "POST",
  });
}

export function reopenThread(commentId: string): Promise<CommentView> {
  return apiFetch<CommentView>(`/comments/${commentId}/reopen`, {
    method: "POST",
  });
}

export function deleteComment(commentId: string): Promise<void> {
  return apiFetch<void>(`/comments/${commentId}`, { method: "DELETE" });
}
