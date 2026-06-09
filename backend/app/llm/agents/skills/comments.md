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
- Flag a problem ("this contradicts the runbook"), ask a question, or note
  context for a specific passage — anything that's *feedback about* the page
  rather than a change to it.
- Prefer a comment over silently editing when you're unsure, when the call is a
  human's to make, or when you want to leave a trail for review.

After commenting, you may share the returned `link` so the human can jump
straight to the thread. Keep comments concise and specific to the quoted span.
