# Modify Wiki

Tools for reading, editing, creating, and reorganizing wiki pages and directories. The wiki only supports `.md` files for content. When you edit, pass the sha you last read for the doc as `base_sha` so the write fails fast with `stale_base` if HEAD has drifted under you.

## Before creating a page — search first

When the user asks for a new doc by *topic* ("write a project spec for web
hooks"), run `search_wiki` on the topic (e.g. "web hooks") **before**
creating anything, and skim the top hits:

- If an existing page already covers it, prefer updating that page — or tell
  the user it exists and ask which they want.
- If related pages exist, use them for placement (create next to them) and
  link to them from the new page instead of duplicating their content.
- Only skip the search when the user explicitly named the page or location
  to create ("create a new page at X" / "make a page called Y").

This is about context, not permission: a create is never blocked — the
search is how you avoid making a duplicate the wiki then has to clean up
(Auto Organize detects duplicates post-hoc, but not creating one is better).

## Tools

- `read_doc(path, sha?)` — Read the full markdown body of a single wiki document at HEAD or at a specific commit SHA. Returns `{path, body, sha, is_head, agents}`. On HEAD reads `agents` lists other users / named agents currently reading or writing the same doc; on historical reads it is empty (we don't preserve activity history). Use this when you need a historical version (e.g. after a `stale_base` error, when comparing against the version you last read). For ordinary HEAD reads, `read_page` is the simpler tool. Pass the returned HEAD `sha` to a subsequent edit as `base_sha` for optimistic-concurrency.
All write tools accept an optional integer `expires_in_seconds` arg (60 to 604800). It sets how long your row stays on the Active agents list — use it when you anticipate continuing on the same file or feature for longer than the 24h default, or want to fade out faster after a short focused edit. There is one row per (user, agent) at a time; the latest write's value wins.

- `edit_doc(path, old_string, new_string, replace_all?, commit_message, base_sha?, expires_in_seconds?)` — surgical find-and-replace on an existing doc. **Default tool for changes.** Include enough surrounding context in `old_string` to make the match unique; minor whitespace drift is tolerated. If multiple edits are to be made in the same doc, prefer using `multi-edit`.
- `multi_edit(path, edits, commit_message, base_sha?, expires_in_seconds?)` — apply several `edit_doc` shaped operations to one file atomically. If any edit fails, none are applied. Prefer over multiple `edit_doc` calls on the same file.
- `write_doc(path, body, commit_message, base_sha?, expires_in_seconds?, template_id?)` — overwrite the entire body of a doc, or create a new file. **Only use for new files or wholesale restructures (>50% of lines changing).** For topic-driven creates, do the search-first check above before calling this. Overwriting an existing file requires `base_sha` (the sha you last read). When **creating** a page, start from a template: call `list_templates`, adapt the chosen template's `body`, and pass that template's id as `template_id` so the new page inherits the template's update policy. If you omit `template_id`, the page defaults to the **Blank** template (auto-update off) — so pick a more specific one when it fits. **Define the page's scope at creation** by passing `update_instruction` (and `ingestion_auto_update_disabled`) — these say what auto-update may change on this page and override the template's defaults. For everything else use `edit_doc` / `multi_edit`.
- `list_templates()` — list the document templates (named starting points for a new page): each template's `id`, `name`, `description`, full `body` (scaffolding to adapt), `auto_update_disabled`, and `update_instruction`. Use before `write_doc` when creating a page to pick the right structure and the policy it implies, then pass the chosen template's `id` to `write_doc` as `template_id`.
- `apply_patch(path, patch, commit_message, base_sha?, expires_in_seconds?)` — Apply a unified-diff patch (one or more `@@ -L,N +L,M @@` hunks) to a wiki document. Atomic: if any hunk fails to apply, nothing is committed. Each hunk is matched line-anchored first; if line drift makes that fail, falls back to the same fuzzy chain `edit_doc` uses (the context + `-` lines must match somewhere uniquely in the file). Use this when you have a line-numbered diff in hand. For find-and-replace edits without line numbers, prefer `edit_doc` or `multi_edit`.
- `update_doc_nl(path, instruction, base_sha?, idempotency_key?, expires_in_seconds?)` — Update a wiki document from a natural-language instruction. Dispatches to the document-updater sub-agent which loads the current body, applies your instruction, and either commits the new body or reports `NO_CHANGE` if nothing should change. Use this for high-level updates like 'mark the TODO under section X as done' or 'add a sentence noting that we shipped Y'. For surgical edits where you already know the exact strings, prefer `edit_doc` / `multi_edit` / `apply_patch` (cheaper, no extra LLM call). USAGE GUIDANCE: this is the most expensive tool in the surface — every call invokes a full LLM pass against the doc body. Batch related context into ONE instruction per logical change ("mark the TODO done and update the status header") rather than firing the tool after every commit or for every individual edit. The server enforces a 30s same-doc debounce; back-to-back calls within that window will return `committed=false reason=debounced`.
- `create_directory(path, commit_message)` — make a new (empty) wiki folder. Use when the user wants a new section before populating it. Only call after the user has explicitly asked for the directory or confirmed it.
- `move_path(old_path, new_path, commit_message)` — rename or relocate a file or directory in one commit; works for `.md` files and folders alike (folders move recursively). Only call after the user has explicitly asked for the move or confirmed it.
- `list_history(path, limit?)` — List the git commit history for a wiki document. Returns commits newest-first as `[{sha, author, ts, message}, ...]`. Useful for finding a historical sha to pass to `read_doc`, or for understanding what's changed recently before proposing an edit. The list follows renames (`git log --follow`).
- `set_update_policy(path, ingestion_auto_update_disabled?, update_instruction?)` — Set how Onyx auto-maintains a page or folder. `ingestion_auto_update_disabled` (bool) turns connector/ingestion auto-update off/on for the page (or everything under a folder); `update_instruction` (string) is free-text guidance the updater follows when it edits (empty string clears it). Both inherit folder→page; a page/subfolder overrides its parent. PATCH semantics — only the settings you pass change. `path` is a `.md` page, a folder, or `""` for the wiki root. Only call when the user asks to change auto-update behavior, not as a side effect of editing content.
