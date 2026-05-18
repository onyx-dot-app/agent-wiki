// Display metadata for tools the chat agent calls.
//
// The backend's chat loop streams ``{type: "tool_call", name, ...}``
// events using the raw, model-facing tool name (``search_wiki``,
// ``edit_doc``, ``load_skill``, …). The names are good for the model
// but not for the user, so this file is the single place that maps
// each name to:
//
//   * ``label`` — a short human verb phrase rendered inline in the
//     chat transcript next to a state icon (spinner / check / ×).
//     Read as the noun-phrase the icon qualifies, so labels stay in
//     the gerund form ("Searching the wiki") and pair naturally with
//     either a running spinner ("Searching the wiki…") or a completed
//     check ("Searching the wiki ✓").
//
//   * ``hidden`` — when true, suppress the tool call from the
//     transcript entirely. Reserved for meta/plumbing calls the user
//     shouldn't care about (today: ``load_skill``).
//
// When adding a new tool on the backend (see ``backend/app/llm/agents/
// skills/__init__.py`` for the canonical list), add a matching entry
// here. Tools without an entry fall back to their raw name so they
// don't disappear — just look slightly out of place — which is the
// cue to come edit this file.

export interface ToolPresentation {
  label: string;
  hidden?: boolean;
}

const TOOL_PRESENTATION: Record<string, ToolPresentation> = {
  // Meta — the model uses ``load_skill`` to unlock additional tools.
  // It's pure plumbing and doesn't represent user-meaningful work.
  load_skill: { label: "Loading skill", hidden: true },

  // Base tools (always available).
  search_wiki: { label: "Searching the wiki" },
  read_page: { label: "Reading a page" },

  // triggers skill
  create_trigger: { label: "Creating a trigger" },
  update_trigger: { label: "Updating a trigger" },
  get_trigger_destinations: { label: "Listing trigger destinations" },

  // modify_wiki skill
  read_doc: { label: "Reading a doc" },
  write_doc: { label: "Writing a doc" },
  edit_doc: { label: "Editing a doc" },
  multi_edit: { label: "Editing a doc" },
  apply_patch: { label: "Applying a patch" },
  move_path: { label: "Moving a file" },
  create_directory: { label: "Creating a folder" },
  update_doc_nl: { label: "Updating a doc" },
  list_history: { label: "Reading edit history" },

  // web_search skill
  web_search: { label: "Searching the web" },
  open_urls: { label: "Opening pages" },

  // ux_explanation skill
  explain_functionality: { label: "Explaining a feature" },
  ask_nl_question: { label: "Asking the wiki" },

  // bash skill
  run_bash: { label: "Running a shell command" },
};

export function presentTool(name: string): ToolPresentation {
  return TOOL_PRESENTATION[name] ?? { label: name };
}
