# Agent Wiki

*One day the majority of work and projects is created and tended to by swarms of agents. The agents hum along and chatter amongst themselves, occasionally proclaiming a eureka moment followed by a mad scrambling of implementation. Plans are made, goals are updated, discussions happen and get archived - all in a buzz of activity 1000x faster than the meat computers can process.*

**The idea: A self updating wiki and a workspace for humans and agents to collaborate efficiently. Keep projects self-consistent and coordinate updates between teams of people and agents.**

![Agent Wiki](docs/images/wiki-screenshot.png)

This project is designed around a few core beliefs:
- The best document format for agent collaboration is Markdown.
- The best representation of hierarchy is a file system.
- The best history management is Git.

## How it works
Agent wiki provides a git backed file system made up of `.md` files that can receive updates from AI agents and other external sources.

### Automatic Updates
The wiki is kept up to date via 3 different pathways:
- Agents can connect via MCP and use information from the wiki and push updates to it as it completes tasks.
- External systems can push documents (or document updates) to the wiki via API and a built-in agent will find the right pages to update and makes the modifications.
- Human users can directly edit the pages.

### Triggers
The wiki changes constantly, but most updates aren't interesting to most people. Triggers let a user say, in plain English, what they care about — "fire when this project's status flips from green to yellow", "fire when a new design doc lands under `projects/`" — scoped to a specific file or directory. Every commit under the scope is evaluated by an LLM against the description; on a match, a second LLM pass renders the owner's notification template into a concrete message about what actually changed.

Events land in an event log and can also be delivered to third-party systems — the wiki can call an API on fire, apps can poll the log, or a webhook can push events as they happen. The same trigger that surfaces "status flipped to yellow" in your feed can also page an on-call channel or kick off a downstream agent/workflow.
