# Frontend Standards

Agent-facing quick reference for `frontend/` conventions. For the architectural
seams (network, auth, wiki authorization) see the root `CLAUDE.md`; for the
full color/button/modal/input styling guide see `frontend/STANDARDS.md`. This
file covers component structure and code-style conventions that apply across
the whole frontend.

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

### Button

See `STANDARDS.md` for the full variant/prominence mapping. Short version:
Opal's `Button` (`@onyx-ai/opal/components`) for new code, or
`src/components/common/Button.tsx` on legacy surfaces that already use it —
don't mix both inside one component.

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

`STANDARDS.md` is canonical for colors, radius, shadows, buttons, modals, and
inputs. Two points worth stating up front since they're easy to get wrong:

- **No CSS Modules.** Style with Tailwind utilities directly on the element
  (`className="flex items-center gap-2 text-(--text-05)"`); pull tokens via
  the `var(--...)` / `text-(--...)` Tailwind-arbitrary-value form. If you
  touch a file that still has a `.module.css`, migrate it to Tailwind and
  delete the module file once it's empty.
- **No raw hex/rgb/named colors, ever** — always an Opal CSS var (`var(--text-05)`)
  or its Tailwind form (`text-text-05`). The one exception is decorative SVG
  illustration glyphs, not UI chrome.

Light mode, dark mode, and the mobile breakpoint (`useIsMobile()` from
`src/lib/viewport.ts`) all need to hold up before a frontend change is done —
Opal's tokens flip automatically with `data-theme`, but verify anyway.

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
