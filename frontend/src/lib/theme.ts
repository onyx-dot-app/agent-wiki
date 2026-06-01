// Centralized design tokens for the agent-wiki frontend.
//
// Tokens resolve to CSS custom properties (e.g. ``var(--color-text-primary)``)
// rather than literal hex values, so toggling the ``.dark`` class on ``<html>``
// swaps the entire UI without re-rendering. The actual hex values for each
// theme live in ``app/globals.css`` keyed off ``:root`` and ``:root.dark``.
// ThemeProvider (``lib/theme-provider.tsx``) reads the user preference and
// toggles both the ``data-theme`` attribute and the ``.dark`` class.
//
// Palette intent: warm greyscale (Notion/Linear-flavored). The "accent"
// role is near-black, not indigo — primary actions stand out by tonal
// contrast rather than hue. Status colors stay separate so banners,
// badges, and danger buttons keep their semantic punch.
//
// Rule of thumb: never write a raw hex in a component. If you need a new
// shade, add it here AND add the matching CSS variable in globals.css for
// both light and dark themes.

export const color = {
  text: {
    primary: "var(--color-text-primary)",
    secondary: "var(--color-text-secondary)",
    muted: "var(--color-text-muted)",
    faint: "var(--color-text-faint)",
    inverse: "var(--color-text-inverse)",
  },
  bg: {
    page: "var(--color-bg-page)",
    panel: "var(--color-bg-panel)",
    sunken: "var(--color-bg-sunken)",
    hover: "var(--color-bg-hover)",
    active: "var(--color-bg-active)",
  },
  border: {
    subtle: "var(--color-border-subtle)",
    default: "var(--color-border-default)",
    strong: "var(--color-border-strong)",
    focus: "var(--color-border-focus)",
  },
  // Primary action surface. "subtle" variants are for selected rows,
  // hover states on item lists, and badges that shouldn't shout.
  accent: {
    bg: "var(--color-accent-bg)",
    bgHover: "var(--color-accent-bg-hover)",
    fg: "var(--color-accent-fg)",
    subtleBg: "var(--color-accent-subtle-bg)",
    subtleBgHover: "var(--color-accent-subtle-bg-hover)",
    subtleFg: "var(--color-accent-subtle-fg)",
    subtleBorder: "var(--color-accent-subtle-border)",
  },
  state: {
    success: {
      bg: "var(--color-state-success-bg)",
      border: "var(--color-state-success-border)",
      fg: "var(--color-state-success-fg)",
    },
    warning: {
      bg: "var(--color-state-warning-bg)",
      border: "var(--color-state-warning-border)",
      fg: "var(--color-state-warning-fg)",
    },
    danger: {
      bg: "var(--color-state-danger-bg)",
      border: "var(--color-state-danger-border)",
      fg: "var(--color-state-danger-fg)",
    },
    info: {
      bg: "var(--color-state-info-bg)",
      border: "var(--color-state-info-border)",
      fg: "var(--color-state-info-fg)",
    },
  },
  // Fixed overlay tint for modal scrims. Warm near-black to match the
  // palette; never use slate (rgba(15,23,42,…)) or pure black.
  overlay: "var(--color-overlay)",
} as const;

export const radius = {
  xs: 4,
  sm: 6,
  md: 8,
  lg: 12,
  pill: 9999,
} as const;

export const shadow = {
  sm: "var(--shadow-sm)",
  md: "var(--shadow-md)",
  popover: "var(--shadow-popover)",
  fab: "var(--shadow-fab)",
  // Centered focal modals (TriggerModal, ShareDialog, history modal).
  // One token so every modal lifts off the page the same amount.
  modal: "var(--shadow-modal)",
  // Side panels anchored to a screen edge (chat widget expanded mode,
  // RunAgentPanel quick-launch).
  panel: "var(--shadow-panel)",
} as const;

// Concrete hex palettes — used by SVG illustrations (the only legal
// raw-hex consumers per CLAUDE.md) when they need to pick the right
// stroke/fill for the active theme. Keep in lock-step with globals.css.
export const lightPalette = {
  textPrimary: "#37352f",
  textSecondary: "#5a5854",
  textMuted: "#787671",
  textFaint: "#9b9a96",
  bgPage: "#ffffff",
  bgPanel: "#fbfbfa",
  bgSunken: "#f7f6f3",
  borderDefault: "#ebebea",
} as const;

export const darkPalette = {
  textPrimary: "#ededec",
  textSecondary: "#bdbcb8",
  textMuted: "#8d8c87",
  textFaint: "#6a6965",
  bgPage: "#1f1d1a",
  bgPanel: "#2a2826",
  bgSunken: "#36332f",
  borderDefault: "#3d3a36",
} as const;

export type PaletteHex = typeof lightPalette;
