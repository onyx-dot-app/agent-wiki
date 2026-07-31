import { ApiError, apiFetch } from "./api";
import type { CommentThreadView, CommentView } from "@/types";

/** What to show a person when a comment action fails.

 * Allowlisted by status rather than by inspecting the text: a server body can
 * be a validation dump naming internal files, or HTML from a proxy, and
 * neither belongs on screen. */
export function commentErrorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    switch (e.status) {
      case 400:
        return "That selection cannot be commented on.";
      case 403:
        return "You do not have permission to comment here.";
      case 404:
        return "That page or comment no longer exists.";
      case 409:
        return "The page changed. Reload and try again.";
      case 413:
        return "That comment is too long.";
      case 429:
        return "Too many requests. Wait a moment and try again.";
    }
  }
  return "Could not save that comment. Try again.";
}

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
