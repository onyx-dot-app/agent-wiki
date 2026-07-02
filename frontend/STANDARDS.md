# Frontend styling standards

## Every change considers light mode, dark mode, and responsiveness

Before declaring a frontend change done, verify it in **both themes** and at
**both viewport sizes**. The app supports light and dark mode (Opal's token
vars flip automatically when `data-theme="dark"` is set on `<html>`) and a
mobile breakpoint (`useIsMobile()` from `src/lib/viewport.ts`).

- Don't introduce raw hex/rgb/named colors — always use Opal CSS vars directly
  (`var(--text-05)`, `var(--background-tint-00)`, etc.) or their Tailwind
  equivalents (`text-text-05`, `bg-background-tint-00`). Shadow composites
  (`--shadow-sm`, `--shadow-md`, etc.) are defined in `globals.css` since Opal
  only provides the raw alpha tokens.
- Don't use `background: "white"` (or any literal) — use `var(--background-tint-00)`.
- Bare `<input>` / `<textarea>` / `<select>` inherit themed defaults from
  `globals.css`; keep them themed when overriding.
- Layouts must hold up at the mobile breakpoint — gate dense desktop
  chrome with `isMobile`, and avoid hardcoded widths that overflow
  narrow viewports.

## Component styling — CSS Modules adjacent, tokens via CSS vars

New components MUST use **CSS Modules**: `Component.module.css` adjacent
to `Component.tsx`, imported as `import styles from "./Component.module.css"`,
applied with `className={styles.foo}`. Next.js scopes class names at
build time so component-local class names (`.card`, `.header`) can stay
short without collision risk.

Cross-cutting rules (page background, native input themes) live in
`src/app/globals.css`. It also defines composite shadow vars (`--shadow-sm`,
`--shadow-md`, etc.) since Opal only provides the raw alpha tokens. Color,
radius, and typography all come from Opal directly — no app-level token
re-exports.

Tokens are Opal CSS vars, consumed as Tailwind utilities or `var(--...)` in
inline styles:

- **Text** — `--text-05` (darkest) … `--text-01` (faintest); `--text-inverted-05`
  for text on dark accent surfaces
- **Backgrounds** — `--background-tint-00` (page/white) … `--background-tint-04`
  (active); `--background-tint-inverted-00/01` for the near-black accent
- **Borders** — `--border-01` (subtle/default) · `--border-02` (strong) · `--border-05` (focus)
- **Status** — `--status-{success|warning|error|info}-01` (bg) · `-02` (border);
  `--status-text-{...}-05` for fg text
- **Overlay** — `--mask-03` for modal scrims
- **Shadows** — `--shadow-sm | --shadow-md | --shadow-popover | --shadow-fab | --shadow-modal | --shadow-panel`
  (composite values defined in `globals.css`)
- **Radius** — `--border-radius-04` (4px) · `--border-radius-08` (8px) · `--border-radius-12` (12px);
  use `rounded-full` for pills

**Rules:**

- Don't write a raw hex (`#xxxxxx`) in a component. Use the Opal CSS var that
  matches the intent (e.g. `var(--background-tint-inverted-00)` for the accent
  surface, not `#1a1a1a`).
- The accent color is **near-black warm grey**, not a hue. Primary buttons,
  selected sidebar avatars, FABs, and active text all use
  `--background-tint-inverted-00`. If you reach for blue/indigo/purple, stop.
- Status colors (`--status-*`) are reserved for semantic signals (banners,
  error toasts, "destructive" buttons). Don't use them as decorative chips —
  use `--background-tint-03` / `--text-05` for that.
- Pick a radius from the scale; don't sprinkle arbitrary integers. Inputs /
  pills inside dense rows = `--border-radius-04`. Buttons / inputs / list items
  = `--border-radius-04`. Cards / popovers / modals = `--border-radius-08`–`12`.
- Decorative SVG icon glyphs (e.g. the amber folder, the blue file icon)
  are the only place raw hex is acceptable — they're illustrations, not UI
  surfaces. Don't extend that exception to anything that paints chrome.

## Buttons — `<Button>` from `@onyx-ai/opal/components` (new code) or `src/components/common/Button.tsx` (existing surfaces)

Opal's `Button` is the preferred primitive for net-new components.
Variant / prominence mapping when migrating existing call sites or
writing new ones:

- accent CTA (form submit, primary) — `variant="action"`
- neutral default — no variant (or `variant="default"`)
- destructive — `variant="danger"`
- low-emphasis text action — `prominence="tertiary"`
- size: `"md"` (default) for forms / modal actions / page headers,
  `"sm"` for dense rows, table cells, inline actions

The legacy `src/components/common/Button.tsx` is kept around as a
shim for the older app pages so we don't have to migrate everything
at once; new launcher-area code uses Opal directly. Either is fine in
isolation — but don't mix them inside the same component.

Don't write ad-hoc `<button style={{ ... }}>` for primary/secondary/danger
chrome. If you need an unusual one-off (icon-only toolbar buttons in
`AppShell` / `ChatWidget`, the wiki row hover actions), keep them inline
but pull every color/radius from Opal tokens — never raw hex.

## Modals — fixed scrim and shadow

All modal-style dialogs (`TriggerModal`, `TriggerHistoryModal`,
`RunAgentModal`, `ShareDialog`) use:

- scrim: `var(--mask-03)` (warm near-black, never slate, never pure black)
- shadow: `var(--shadow-modal)`
- radius: `var(--border-radius-12)` for the surface
- buttons: `<Button>`, with the action row at `justifyContent: flex-end`,
  Cancel first, primary action last

Side panels anchored to a screen edge use `var(--shadow-panel)`.

### Destructive confirmations — `useConfirm`, never `window.confirm`

Don't call the browser's `confirm()` for delete/revoke/clear warnings — use
the in-app dialog from `src/components/common/ConfirmDialog.tsx`:

```tsx
const confirmDialog = useConfirm();
if (!(await confirmDialog({
  title: "Delete this trigger?",
  body: "Optional supporting line.",   // optional
  confirmLabel: "Delete",              // the danger button label
}))) return;
```

`ConfirmProvider` is mounted once in the root layout. The only sanctioned
`window.confirm` left is the unsaved-changes navigation guard in the wiki
page, which must synchronously block a click event.

## Inputs / selects — Opal components first

New UI composes from `@onyx-ai/opal/components` rather than raw controls:
`InputTypeIn` for text/search inputs, `SelectButton` + `Popover`/`PopoverMenu`
+ `LineItemButton` for pickers and dropdowns, `Switch`, `Checkbox`, `Tabs`.
A raw `<input>`/`<select>` in new code is a review flag — reach for it only
where Opal has no equivalent, and then style it per the rules below.

Where a raw control is genuinely required, form inputs and `<select>`
controls use:

- border: `1px solid var(--border-01)` (use `var(--border-02)` only
  for emphasis — most inputs should be `border-01`)
- radius: `var(--border-radius-04)`
- padding: `8px 10px` (or `padding: 8` for compact contexts)

Don't set `appearance: "auto"` on a `<select>` — it bypasses the rest of
the styling and produces a native control next to custom-looking ones.

Don't accumulate parallel ad-hoc colors. If a shade you need is missing from
Opal, file a request — don't invent `--color-*` aliases.

## Components

- Functional, typed props.
- Server components are fine, but anything reading auth must be `"use client"`.
- Place reusable components under `src/components/<area>/` and route-scoped
  components co-located with the route.

## Network — only via `src/lib/api.ts:apiFetch`

`apiFetch<T>(path, init?)` sets `credentials: "include"`, JSON content type,
and parses the `{error}` envelope into `ApiError` with a `.status`. Don't
call `fetch` directly.

## Auth — only via `src/lib/auth.tsx`

Pages call `useRequireAuth()` to gate, `useAuth()` to read state. Don't
call `/api/auth/me` from a component — let the provider own that. New auth
flows (e.g. password reset) extend the context, not the pages.

## Markdown

Use `react-markdown` + `remark-gfm` (already wired in the wiki page). Don't
inject HTML from the backend.
