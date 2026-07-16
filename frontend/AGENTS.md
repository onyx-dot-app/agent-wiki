# Frontend Standards

Agent-facing quick reference for `frontend/` conventions — component
structure, styling, and code-style. For the architectural seams (network,
auth, wiki authorization) see the root `CLAUDE.md`.

## Directory layout

```
src/app/            Next.js routes — thin. A route's page.tsx is a one-line
                     re-export of its view (see src/views/ below); Next.js's
                     App Router requires the file to live there, but no logic
                     does.
src/views/<area>/    Route-level orchestration for one top-level route
                     (WikiPage.tsx, TriggersPage.tsx, …) — the actual page
                     component that src/app/.../page.tsx re-exports.
src/lib/<feature>/   Feature-scoped non-component code + components together:
                     types.ts, hooks.ts, svc.ts (API calls), utils.ts,
                     components.tsx. See `coeditor/` and `fileview/` for the
                     canonical shape. Smaller features skip the split and
                     keep everything in one `lib/<feature>.ts`.
src/components/<area>/  Reusable components used from more than one route.
src/sections/sidebar/   Sidebar-specific composite components.
src/providers/       React context providers.
src/hooks/            Genuinely cross-cutting hooks with no feature home
                     (useAppFocus, useToast) — last resort; see "Hooks"
                     below.
```

## Components

The design system is **Opal** (`@onyx-ai/opal`), imported by subpath:

```typescript
import { Button, Text, Divider, Popover } from "@onyx-ai/opal/components";
import { Content, ContentAction } from "@onyx-ai/opal/layouts";
import { SvgFile, SvgTrash } from "@onyx-ai/opal/icons";
```

- Functional, typed props.
- Server components are fine, but anything reading auth must be `"use client"`.
- Place reusable components under `src/components/<area>/`; route-scoped
  components co-located with the route.

### Content (`@onyx-ai/opal/layouts`)

**Use this for any combination of icon + title + description** — including
page/document titles. It routes to an internal layout based on `sizePreset` +
`variant`:

| sizePreset | variant | Layout |
|---|---|---|
| `headline` / `section` | `heading` | Icon on top, large — page/document titles |
| `headline` / `section` | `section` | Icon inline, large |
| `main-content` / `main-ui` / `secondary` | `section` (default) | Compact inline — panel/section headers |
| `main-content` / `main-ui` / `secondary` | `body` | Body text layout |

Don't reach for `main-ui`/`section` (the compact tier) when the content is
actually a document or page heading — that's `headline`/`heading`.

### Text (`@onyx-ai/opal/components`)

**Never render a raw `<p>` or naked text node for UI copy — use `Text`.**

```typescript
<Text font="main-ui-action" color="text-05">{name}</Text>
```

- `font`: `TextFont` — e.g. `"heading-h1"`, `"main-ui-body"`, `"secondary-action"`
- `color`: `TextColor` — e.g. `"text-03"`, `"text-05"`, `"status-error-05"`
- `as`: HTML tag override (default `"span"`)

### Buttons — `<Button>` from `@onyx-ai/opal/components` (new code) or `src/components/common/Button.tsx` (existing surfaces)

Opal's `Button` is the preferred primitive for net-new components. Variant /
prominence mapping when migrating existing call sites or writing new ones:

- accent CTA (form submit, primary) — `variant="action"`
- neutral default — no variant (or `variant="default"`)
- destructive — `variant="danger"`
- low-emphasis text action — `prominence="tertiary"`
- size: `"md"` (default) for forms / modal actions / page headers, `"sm"` for
  dense rows, table cells, inline actions

The legacy `src/components/common/Button.tsx` is kept around as a shim for
older app pages so we don't have to migrate everything at once; new
launcher-area code uses Opal directly. Either is fine in isolation — don't
mix them inside the same component.

Don't write ad-hoc `<button style={{ ... }}>` for primary/secondary/danger
chrome. If you need an unusual one-off (icon-only toolbar buttons, wiki row
hover actions), keep it inline but pull every color/radius from Opal
tokens — never raw hex.

### Function components, not arrow-function consts

```typescript
// ✅ Good
function DocTitle({ path }: DocTitleProps) { ... }

// ❌ Bad
const DocTitle = ({ path }: DocTitleProps) => { ... };
```

(A `memo()`-wrapped component is the one exception — wrap a named function
expression: `export const Foo = memo(function Foo({ ... }: FooProps) { ... })`.)

### Props interface extraction

**Extract every component's prop type into a named `${ComponentName}Props`
interface in the same file, placed immediately above the component** (before
its JSDoc comment, if any). Never destructure props against an inline object
type literal.

```typescript
// ✅ Good
interface UserCardProps {
  user: User;
  showActions?: boolean;
}

function UserCard({ user, showActions = false }: UserCardProps) { ... }

// ❌ Bad — inline prop type
function UserCard({
  user,
  showActions = false,
}: {
  user: User;
  showActions?: boolean;
}) { ... }
```

Non-prop types (API response shapes, shared domain models) belong in the
feature's `types.ts`, not inline in the component file.

## Styling

Every change considers **light mode, dark mode, and responsiveness** before
it's done. The app supports light/dark (Opal's token vars flip automatically
when `data-theme="dark"` is set on `<html>`) and a mobile breakpoint
(`useIsMobile()` from `src/lib/viewport.ts`) — verify both.

- Don't introduce raw hex/rgb/named colors — always an Opal CSS var directly
  (`var(--text-05)`, `var(--background-tint-00)`) or its Tailwind equivalent
  (`text-text-05`, `bg-background-tint-00`). The one exception is decorative
  SVG illustration glyphs, not UI chrome.
- Don't use `background: "white"` (or any literal) — use `var(--background-tint-00)`.
- Bare `<input>` / `<textarea>` / `<select>` inherit themed defaults from
  `globals.css`; keep them themed when overriding.
- Layouts must hold up at the mobile breakpoint — gate dense desktop chrome
  with `isMobile`, and avoid hardcoded widths that overflow narrow viewports.

### No CSS Modules — Tailwind inline, tokens via CSS vars

Style components with Tailwind utility classes directly on the element
(`className="flex items-center gap-2 text-(--text-05)"`); pull Opal tokens
via the `var(--...)` / `text-(--...)` Tailwind-arbitrary-value form. If you
touch a file that still has an adjacent `Component.module.css`, migrate its
rules to Tailwind utilities on the JSX and delete the module file once it's
empty — don't add new `.module.css` files.

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

Rules:

- The accent color is **near-black warm grey**, not a hue. Primary buttons,
  selected sidebar avatars, FABs, and active text all use
  `--background-tint-inverted-00`. If you reach for blue/indigo/purple, stop.
- Status colors (`--status-*`) are reserved for semantic signals (banners,
  error toasts, "destructive" buttons). Don't use them as decorative chips —
  use `--background-tint-03` / `--text-05` for that.
- Pick a radius from the scale; don't sprinkle arbitrary integers. Inputs /
  pills inside dense rows and buttons/list items = `--border-radius-04`.
  Cards / popovers / modals = `--border-radius-08`–`12`.
- Don't accumulate parallel ad-hoc colors. If a shade you need is missing
  from Opal, file a request — don't invent `--color-*` aliases.

### Modals — fixed scrim and shadow

All modal-style dialogs (`TriggerModal`, `TriggerHistoryModal`,
`RunAgentModal`, `ShareDialog`) use:

- scrim: `var(--mask-03)` (warm near-black, never slate, never pure black)
- shadow: `var(--shadow-modal)`
- radius: `var(--border-radius-12)` for the surface
- buttons: `<Button>`, action row at `justifyContent: flex-end`, Cancel
  first, primary action last

Side panels anchored to a screen edge use `var(--shadow-panel)`.

**Destructive confirmations use `useConfirm`, never `window.confirm`:**

```tsx
const confirmDialog = useConfirm();
if (!(await confirmDialog({
  title: "Delete this trigger?",
  body: "Optional supporting line.",   // optional
  confirmLabel: "Delete",              // the danger button label
}))) return;
```

`ConfirmProvider` (`src/components/common/ConfirmDialog.tsx`) is mounted once
in the root layout. The only sanctioned `window.confirm` left is the
unsaved-changes navigation guard in the wiki page, which must synchronously
block a click event.

### Inputs / selects — Opal components first

New UI composes from `@onyx-ai/opal/components` rather than raw controls:
`InputTypeIn` for text/search inputs, `SelectButton` + `Popover`/`PopoverMenu`
+ `LineItemButton` for pickers and dropdowns, `Switch`, `Checkbox`, `Tabs`. A
raw `<input>`/`<select>` in new code is a review flag — reach for it only
where Opal has no equivalent.

Where a raw control is genuinely required:

- border: `1px solid var(--border-01)` (`var(--border-02)` only for emphasis)
- radius: `var(--border-radius-04)`
- padding: `8px 10px` (or `padding: 8` for compact contexts)
- don't set `appearance: "auto"` on a `<select>` — it bypasses the rest of
  the styling and produces a native control next to custom-looking ones

### Markdown

Use `react-markdown` + `remark-gfm` (already wired in the wiki page). Don't
inject HTML from the backend.

## Data fetching — SWR

- One hook per resource, colocated with that feature's other code
  (`lib/<feature>/hooks.ts`, or inline in `lib/<feature>.ts` for a smaller
  feature). The hook owns the `useSWR` call, the key, and the shaped return
  (`{ data, error, isLoading, refresh }`-style) — components don't call
  `useSWR` directly.
- Every `useSWR` key goes through `lib/swr-keys.ts:SWR_KEYS` — a static
  string for a fixed endpoint, or a builder function for a dynamic/
  query-string/tuple key. Never an inline string or template literal as a
  `useSWR` key.
- Fetch client-side, in the component that needs the data, with its own
  loading/placeholder state — not fetched at a parent and passed down.

## Hooks organization

Priority order, same as the component-location rule above:

1. **Feature hook** (`lib/<feature>/hooks.ts` or `lib/<feature>.ts`) — if the
   hook is specific to a domain (wiki, triggers, permissions, …), it lives
   with the rest of that feature's code.
2. **`src/hooks/`** — last resort, for hooks with no feature home and nothing
   domain-specific about them (`useAppFocus`, `useToast`).

## Imports

Always absolute, via the `@/` prefix — never a relative `../../` chain.

```typescript
// ✅ Good
import { useAuth } from "@/lib/auth";

// ❌ Bad
import { useAuth } from "../../lib/auth";
```
