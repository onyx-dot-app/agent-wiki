# difficult_separable_work

## LLM layer
- Support for OpenAI (Responses API), Anthropic, Gemini, Ollama, no need for any of the rest for now. Use their client libraries directly
- Support streaming, tool calling, reasoning
- Need a basic chat loop, don't use any custom frameworks, very easy to build internally, no branching, no edits, nothing fancy.

## MCP for agents
**→ Designed in [mcp-server/mcp-server.md](mcp-server/mcp-server.md).** Phased
implementation plan, transport (Streamable HTTP), per-user tokens, tool
inventory (`read_doc(sha?)`, `apply_patch`, `update_doc_nl`,
`ask_nl_question`, …), `wiki://` and `job://` resource subscriptions, and
the `base_sha` + push-notification staleness model are all spelled out
there. Update that doc, not this section, going forward.

Original brief retained for traceability:
- Fetch the relevant wikis for a project (bm25 based, LLM filtering/selection). Gives back the full MDs and provides it as a resource
- Pass updates via the resource design: https://modelcontextprotocol.info/docs/concepts/resources/
- Write an update to the wiki using a natural language description: "I have now completed the TODO under section X which is blah blah blah"
- Write an update to the wiki using a +/- diff format.
- I think this does not need a skill but if it's not reliable with OpenCode + Claude Code + Codex, then we may need to find a way to have a skill for this.
- It needs to tell the agent how frequently to update the doc, some heuristics or explanations so that it's not overwhelming.
- Needs some API/key creation thing to hook up agents via MCP easily, probably should be linked to a particular user.
- Need to provide a git history view of the file also, on every change, the commit message should be something short and semantically meaningful

## Document update agent harness (agent only)
- Needs to be able to take either a natural language description or a +/- diff format.
  - For the description, it needs to ensure that over time the doc does not massively bloat.
  - For the +/- it should try to apply it at the specified lines, if not, it should check if there is only 1 place which it can be applied, if both fail, then it just returns a descriptive error to the agent.
  - Probably the doc should have a changelog at the bottom. It's natural language and LLM generated, users can ask to summarize it if it gets too long.
- The harness likely needs things like a update_doc tool which looks similar to how Claude Code or Opencode does it.
- Needs an API to receive these types of updates
- 2 Endpoints on the server side.
  - POST /api/mcp/update — validate, insert jobs row (pending), enqueue Huey task, return {job_id}. Returns in ms; no DB session or worker
  held.
  - GET /api/jobs/<id>?wait=<sec> — long-poll: check jobs row, if pending wait on an Event (or short sleep loop) up to wait, return current
   status. Brief DB session per call.
  - Retry is the real hazard. If the agent gives up and re-POSTs the same edit, you get a duplicate commit. Defend against that with an
  idempotency key on the POST (hash of path + content + agent_id, or a client-supplied key) — the route checks for an existing job with
  that key and returns its job_id instead of enqueueing a new one.
- Need everything to be backed by GIT. This needs to run immediately and if it fails, the call should fail.
- Separately on document update, it needs to queue a reindex into the BM25 index.

## Background tasks
- 3 queues: one for document updates using the LLM, another for triggers, another for BM25 processing
- Cron: for periodic checks for certain triggers, throws it into the triggers queue
- Need to consider failure cases since these are async, what happens if workers dies, tasks are lost, etc.
  - It's ok for now if a task/trigger gets missed for some reason, it should just have a reasonably retry and then we track a failure somewhere
  - We need to have some table(s) to audit all the fired events and if it was acknowledged by the downstream system (where applicable)

## Git system
- All document/trigger versions are saved. Triggers for example can be saved in the system as .trigger_1_document_name.md or .trigger_1 for folder level, and it can sit in those folders.
- Documents on agent write or user edit + save, creates a commit with something like revision_x monotonically increasing
- If a user rolls back to a previous version, we can just mark in later commits with some special prefix pattern to the commit message like IS_A_DEPRECATED_DOC_VERSION or something and not display any of those in the history view of the page
  - **Built (rollback flow):** the wiki page now has a "History" panel that lists per-file commits and lets you view any prior version. Editing from an older version sends `base_sha` to `PUT /api/documents/file`; the new commit body gets a `Deprecates: <sha>...` trailer listing every commit between `base_sha` and the head that touched the file. `GET /api/documents/file/history` walks all commits and hides any sha that appears in any later commit's `Deprecates:` trailer, so rolled-back-over revisions disappear without rewriting git history. See `backend/app/wiki/git.py` (`head_sha_for_path`, `commits_between`, body-aware `history`), `backend/app/api/documents.py` (`file_history`, `base_sha` on PUT, `?ref=` on GET), and `frontend/src/app/wiki/[[...slug]]/page.tsx` (`HistoryPanel`).

## MCP personal token
- Need a page to create a personal token for this

## UI Pages
- Page to create API keys at a user level to connect up their agents
- Need to have a sidebar chat that's expandable and collapsible