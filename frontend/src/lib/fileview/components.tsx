"use client";

/** Memoized markdown renderer for wiki file content.
 *
 * Encapsulates the full plugin stack used by the file viewer: GFM tables/task
 * lists, space-link re-encoding, and source-position annotation for comment
 * anchoring. `React.memo` means the article only re-renders when `body` changes,
 * which preserves CSS Highlight API ranges across unrelated re-renders (panel
 * open/close, active-comment changes, etc.).
 */
import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { remarkBareSpaceLinks } from "@/lib/remarkBareSpaceLinks";
import { rehypeSourcePos } from "@/lib/fileview/rehypeSourcePos";

export const MarkdownRenderer = memo(function MarkdownRenderer({
  body,
}: {
  body: string;
}) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkBareSpaceLinks]}
      rehypePlugins={[rehypeSourcePos]}
    >
      {body}
    </ReactMarkdown>
  );
});
