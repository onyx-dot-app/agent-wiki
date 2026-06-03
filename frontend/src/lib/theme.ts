// Design tokens for the agent-wiki frontend.
//
// All values resolve to CSS custom properties backed by Opal's semantic
// tokens (defined in `@onyx-ai/opal/root.css`). The app's own `globals.css`
// aliases the `--color-*`, `--shadow-*`, and `--radius-*` vars onto the
// relevant Opal vars so that dark-mode flips happen automatically when
// Opal's `.dark` class is toggled on `<html>`.
//
// Rule of thumb: never write a raw hex in a component. If a shade you need
// is missing, add it to `globals.css` (pointing at an Opal var) and expose
// it here.

export const color = {
  text: {
    primary:   "var(--color-text-primary)",
    secondary: "var(--color-text-secondary)",
    muted:     "var(--color-text-muted)",
    faint:     "var(--color-text-faint)",
    inverse:   "var(--color-text-inverse)",
  },
  bg: {
    page:   "var(--color-bg-page)",
    panel:  "var(--color-bg-panel)",
    sunken: "var(--color-bg-sunken)",
    hover:  "var(--color-bg-hover)",
    active: "var(--color-bg-active)",
  },
  border: {
    subtle:  "var(--color-border-subtle)",
    default: "var(--color-border-default)",
    strong:  "var(--color-border-strong)",
    focus:   "var(--color-border-focus)",
  },
  // Primary action surface. "subtle" variants are for selected rows,
  // hover states on item lists, and badges that shouldn't shout.
  accent: {
    bg:            "var(--color-accent-bg)",
    bgHover:       "var(--color-accent-bg-hover)",
    fg:            "var(--color-accent-fg)",
    subtleBg:      "var(--color-accent-subtle-bg)",
    subtleBgHover: "var(--color-accent-subtle-bg-hover)",
    subtleFg:      "var(--color-accent-subtle-fg)",
    subtleBorder:  "var(--color-accent-subtle-border)",
  },
  state: {
    success: {
      bg:     "var(--color-state-success-bg)",
      border: "var(--color-state-success-border)",
      fg:     "var(--color-state-success-fg)",
    },
    warning: {
      bg:     "var(--color-state-warning-bg)",
      border: "var(--color-state-warning-border)",
      fg:     "var(--color-state-warning-fg)",
    },
    danger: {
      bg:     "var(--color-state-danger-bg)",
      border: "var(--color-state-danger-border)",
      fg:     "var(--color-state-danger-fg)",
    },
    info: {
      bg:     "var(--color-state-info-bg)",
      border: "var(--color-state-info-border)",
      fg:     "var(--color-state-info-fg)",
    },
  },
  // Fixed overlay tint for modal scrims.
  overlay: "var(--color-overlay)",
} as const;

// Radius tokens — CSS vars backed by Opal's border-radius scale.
// Safe to use in both className (`rounded-(--radius-sm)`) and inline
// styles (`borderRadius: radius.sm`).
export const radius = {
  xs:   "var(--radius-xs)",
  sm:   "var(--radius-sm)",
  md:   "var(--radius-md)",
  lg:   "var(--radius-lg)",
  pill: "9999px",
} as const;

export const shadow = {
  sm:      "var(--shadow-sm)",
  md:      "var(--shadow-md)",
  popover: "var(--shadow-popover)",
  fab:     "var(--shadow-fab)",
  modal:   "var(--shadow-modal)",
  panel:   "var(--shadow-panel)",
} as const;
