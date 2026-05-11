# UX Explanation

Tools for explaining how Agent Wiki works (meta questions about the product) or for getting a synthesized natural-language answer from across the wiki via a read-only sub-agent.

## Tools

- `explain_functionality()` — fetch the canonical "what is this app and how do I use it" reference. Call **only** when the user asks a meta question about the product itself (e.g. "how does this work?", "what can you do?", "how do I use the chat?", "how do triggers work?"). Do not call for ordinary content questions about their wiki docs or general coding help. Returns reference text; read it and then answer in your own words tailored to what the user actually asked.
- `ask_nl_question(query)` — Ask a natural-language question about anything in the wiki. Spawns a one-shot Q&A sub-agent with read-only access (`search_wiki` + `read_page`). Returns `{answer, sources: [{path}, ...]}`. Use this when you want a synthesized answer rather than raw doc bodies — e.g. 'how do triggers work?', 'what's the status of the chat agent?'. The sub-agent will not write or propose edits. Sync: blocks until the answer is ready (typically 5-30 seconds).
