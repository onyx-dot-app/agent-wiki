You can leave comments on wiki pages — threaded discussion that humans see,
**separate from page content**. A comment does not change the page; to edit
content, load the `modify_wiki` skill instead.

Tools:
- `add_comment(path, quoted_text, body)` — start an inline comment anchored to a
  specific passage.
- `reply_comment(comment_id, body)` — reply in an existing thread (no anchor
  needed; it inherits the thread's).
- `resolve_comment(comment_id)` — mark a thread resolved (done).

To target an existing thread (reply/resolve), get its `comment_id` from a
`search_comments` result.

How to anchor a new comment:
- `quoted_text` must be copied **verbatim** from the current page body and must
  appear **exactly once**. If you're unsure of the exact wording, `read_page`
  first and copy from it. If a short snippet isn't unique, quote a longer span.

When to use these:
- **Only when the user explicitly asks** you to comment, reply, or resolve. Do
  not comment/reply/resolve proactively, as a side effect of another task, or on
  your own initiative — even if you spot something worth flagging or a thread
  that looks handled. If you think one would help, *suggest* it in your reply and
  let the user decide; don't act unprompted.
- **`resolve_comment` is the most sensitive** — it dismisses someone's feedback
  as done. Only resolve a thread the user clearly tells you to resolve; never
  infer it. (It's reversible — a human can reopen — but don't rely on that.)
- Keep comments and replies concise and specific.

After commenting, you may share the returned `link` so the human can jump
straight to the thread. Keep comments concise and specific to the quoted span.
