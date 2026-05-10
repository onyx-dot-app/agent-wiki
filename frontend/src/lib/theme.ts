// Centralized design tokens for the agent-wiki frontend.
//
// Why a constant module instead of CSS variables: the codebase is
// inline-style React (no Tailwind, no CSS-in-JS), and TS constants stay
// type-checked, autocomplete cleanly, and survive refactors.
//
// Palette intent: warm greyscale (Notion/Linear-flavored). The "accent"
// role is near-black, not indigo — primary actions stand out by tonal
// contrast rather than hue. Status colors stay separate so banners,
// badges, and danger buttons keep their semantic punch.
//
// Rule of thumb: never write a raw hex in a component. If you need a new
// shade, add it here.

export const color = {
  text: {
    primary: "#37352f",
    secondary: "#5a5854",
    muted: "#787671",
    faint: "#9b9a96",
    inverse: "#ffffff",
  },
  bg: {
    page: "#ffffff",
    panel: "#fbfbfa",
    sunken: "#f7f6f3",
    hover: "#efeeec",
    active: "#e8e7e4",
  },
  border: {
    subtle: "#f1f0ee",
    default: "#ebebea",
    strong: "#d9d8d5",
    focus: "#37352f",
  },
  // Primary action surface. "subtle" variants are for selected rows,
  // hover states on item lists, and badges that shouldn't shout.
  accent: {
    bg: "#37352f",
    bgHover: "#1f1d1a",
    fg: "#ffffff",
    subtleBg: "#f1f0ee",
    subtleBgHover: "#e8e7e4",
    subtleFg: "#37352f",
    subtleBorder: "#dcdbd8",
  },
  state: {
    success: { bg: "#dcfce7", border: "#86efac", fg: "#166534" },
    warning: { bg: "#fef3c7", border: "#fcd34d", fg: "#78350f" },
    danger: { bg: "#fee2e2", border: "#fca5a5", fg: "#7f1d1d" },
    info: { bg: "#e0f2fe", border: "#7dd3fc", fg: "#075985" },
  },
  // Fixed overlay tint for modal scrims. Warm near-black to match the
  // palette; never use slate (rgba(15,23,42,…)) or pure black.
  overlay: "rgba(15, 15, 15, 0.45)",
} as const;

export const radius = {
  xs: 4,
  sm: 6,
  md: 8,
  lg: 12,
  pill: 9999,
} as const;

export const shadow = {
  sm: "0 1px 2px rgba(15, 15, 15, 0.06)",
  md: "0 4px 12px rgba(15, 15, 15, 0.08)",
  popover: "0 8px 24px rgba(15, 15, 15, 0.10)",
  fab: "0 6px 20px rgba(15, 15, 15, 0.18)",
  // Centered focal modals (RunAgentModal, TriggerModal, ShareDialog,
  // history modal). One token so every modal lifts off the page the
  // same amount.
  modal: "0 24px 60px rgba(15, 15, 15, 0.18)",
  // Side panels anchored to a screen edge (chat widget expanded mode).
  panel: "-4px 0 24px rgba(15, 15, 15, 0.08)",
} as const;
