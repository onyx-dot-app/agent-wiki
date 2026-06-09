You can leave comments on wiki pages — threaded discussion that humans see,
**separate from page content**. A comment does not change the page; to edit
content, load the `modify_wiki` skill instead.

Tool:
- `add_comment(path, quoted_text, body)` — leave an inline comment anchored to a
  specific passage.

How to anchor:
- `quoted_text` must be copied **verbatim** from the current page body and must
  appear **exactly once**. If you're unsure of the exact wording, `read_page`
  first and copy from it. If a short snippet isn't unique, quote a longer span.

When to use it:
- **Only when the user explicitly asks you to leave / post / add a comment.**
  Do not comment proactively, as a side effect of another task, or on your own
  initiative — even if you spot something worth flagging. If you think a comment
  would help, *suggest* it in your reply and let the user decide; don't post one
  unprompted.
- Once the user asks, comment on the specific passage they mean (anchored to a
  quoted snippet) and keep it concise.

After commenting, you may share the returned `link` so the human can jump
straight to the thread. Keep comments concise and specific to the quoted span.
