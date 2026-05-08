# agent-wiki — what it is and how to use it

agent-wiki is a self-updating wiki where humans and AI agents collaborate on living documentation. It runs as three things stitched together:

## The wiki

A git-backed tree of markdown files, browseable in the **Wiki** tab. Click a folder to see its contents; click a `.md` file to read it; toggle into the editor to make changes. Every save is a real git commit, so history is preserved and recoverable. Files outside `.md` are not supported — the wiki is markdown-only.

## The chat agent (this conversation)

The chat panel — accessible from any page — is a tool-using AI agent that knows the current wiki location and can act on the user's behalf. It can:

- **Search and read** the wiki: `search_wiki` finds candidate docs by bm25; `read_page` returns the full body of one.
- **Edit and write** docs: `edit_doc` for surgical find-and-replace, `multi_edit` for atomic batch edits, `write_doc` for new files or wholesale rewrites. Also `create_directory` and `move_path` for tree changes.
- **Reach the public web**: `web_search` (used sparingly, only for things that may need recent info) and `open_url` to fetch a single page.
- **Manage triggers**: `create_trigger` and `update_trigger`.

The agent always confirms changes in chat before committing them — describe → wait for acknowledgement → apply. After an edit it confirms what was committed and surfaces any broken links it noticed.

## Triggers and events

A **trigger** watches a wiki file or directory and fires when an update matches a natural-language condition. Each trigger has three parts:

- **If condition** — what kind of change should fire it (e.g. "when the auth flow's session timeout policy changes").
- **Message** — the notification body delivered when it fires.
- **Destination** — where to deliver. In v0 the only destination is the **Event Log**: every fire is recorded there with the message attached.

Triggers are owned by the user who created them and are listed in the **Triggers** tab. The **Events** tab shows fire history.

## Admin

The **Admin** area (visible only to admins) holds configuration:

- **LLM provider** — set the model and API key for the chat agent.
- **Web** — set the Serper (search) and Firecrawl (page fetch) API keys; without these the web tools error with "not configured."
- **Users** — basic user management.

The first account created on a fresh install is auto-promoted to admin.

## Typical flows

- **Ask a question about your wiki** — the agent searches first, reads the most relevant page(s), and answers with citations to the wiki paths.
- **Edit a doc** — describe what you want changed; the agent reads the doc, proposes a diff, and commits after you say go.
- **Set up a notification** — tell the agent "let me know when the X policy changes": it'll create a trigger with an if-condition matching that change and a message you'll see in the Event Log when it fires.
- **Pull in outside info** — for current events, third-party docs, or news, the agent can web-search and open a URL, citing sources back in chat or in any doc it writes.
