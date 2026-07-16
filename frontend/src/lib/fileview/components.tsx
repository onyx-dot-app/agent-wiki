"use client";

import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Content } from "@onyx-ai/opal/layouts";
import { Divider } from "@onyx-ai/opal/components";
import { remarkBareSpaceLinks } from "@/lib/remarkBareSpaceLinks";
import { rehypeSourcePos } from "@/lib/fileview/rehypeSourcePos";
import { pageTitle } from "@/lib/fileview/utils";

/** Renders the page title and a divider below it. */
export function DocTitle({ path }: { path: string }) {
  return (
    <>
      <Content sizePreset="main-ui" variant="section" title={pageTitle(path)} />
      <Divider />
    </>
  );
}

/** Memoized markdown renderer for wiki file content.
 *
 * Encapsulates the full plugin stack: GFM tables/task lists, space-link
 * re-encoding, and source-position annotation for comment anchoring.
 * `React.memo` means the article only re-renders when `body` changes, which
 * preserves CSS Highlight API ranges across unrelated re-renders.
 */
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
