# Bash

Backup tool for running read-only shell commands against the wiki tree when the more targeted tools don't provide enough flexibility.

## Tools

- `run_bash(command)` — read-only Unix command against the wiki tree. **This is a backup tool — reach for it only when the user is asking a wiki-related question and the other tools don't provide enough flexibility.** Good fits: counting files in a directory, listing the tree, finding a literal string across the whole wiki with line numbers, scanning many docs in one pass. Whitelist commands: `cat, find, grep, ls, head, tail, wc`. Pipes / `&&` / `||` / `;` work; anything outside the whitelist (`rm`, `mv`, `git`, `bash`, redirects) is rejected before execution.
