# Help Wanted

## Infra
- [ ] Get the deployment up to date and live

## Features
- [ ] Start Claude Code / Codex from the button with the context. Ask the user to set up MCP if not already.
- [ ] Ensure document updates are correctly received by connected MCP agents.
- [ ] Start Craft with this
- [ ] Document updates from Onyx push
  - [ ] Architecture reviewed first
  - [ ] Agent harness reviewed

## LLM Hardening
- [ ] The supported LLM providers are probably broken around the edges
- [ ] We probably want to let the user choose the default reasoning level (default to medium)
- [ ] The configuration process for LLMs is horrendous and there is no validation

## UI/UX
- [ ] The entire admin panel needs a makeover, especially the LLMs
- [ ] Chat streaming in the widget/sidepanel is not working
- [ ] Need user invitation functionality
- [ ] Need password reset

## Hardening
- [ ] I think there are likely edge cases with the background jobs that may cause problems on failures, restarts, etc.
- [ ] Audit of the major areas, sanity check the LLM generated code briefly
- [ ] Evaluating the scalability of the git approach and if any optimizations are needed for that
