"""Chat agent backing the in-app ChatUI.

Tools the chat agent should expose (sketch):
  * search_wiki(query)             — bm25 over docs_fts
  * read_doc(path)                 — git read
  * propose_doc_edit(path, body)   — emits a draft diff for the user to accept
  * list_my_triggers()             — current user's triggers
  * upsert_trigger(...)            — create/update trigger owned by current user
  * delete_trigger(id)
"""
from __future__ import annotations


def run_chat_turn(user_id: str, conversation_id: str, message: str) -> dict:
    """Run one user turn through a multi-iteration tool-using loop."""
    # TODO: assemble system prompt, prior turns, tools list; loop until the
    # model emits a final assistant message; persist the conversation;
    # return the streamed response payload.
    raise NotImplementedError
