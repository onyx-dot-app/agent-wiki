# Agent Wiki

*One day the majority of work and projects is created and tended to by swarms of agents. The agents hum along and chatter amongst themselves, occasionally proclaiming a eureka moment followed by a mad scrambling of implementation. Plans are made, goals are updated, discussions happen and get archived - all in a buzz of activity 1000x faster than the old biological computers can process. -not@karpathy, May 2026*

The idea: A self updating wiki and a workspace for humans and agents to collaborate efficiently. Keep projects self-consistent and coordinate updates between teams of people and agents.

This project is designed around a few core beliefs:
- The best document format for agent collaboration is Markdown.
- The best representation of hierarchy is a file system.
- The best history management is Git.

## How it works
Agent wiki provides a git backed file system made up of `.md` files that can receive updates from AI agents and other external sources.
