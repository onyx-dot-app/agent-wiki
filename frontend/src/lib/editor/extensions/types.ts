import type { Editor, Range } from "@tiptap/core";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";

/** One selectable slash-menu command — the shared shape between the command
 * set + filtering (`commandMenu.ts`) and the menu UI (`CommandList` in
 * `components.tsx`). It lives here, in a types-only module, so `components.tsx`
 * can reference it with an `import type` (erased at build — no runtime
 * `extensions/` edge) rather than a value import from the extensions layer. */
export interface CommandItem {
  title: string;
  icon: IconFunctionComponent;
  run: (editor: Editor, range: Range) => void;
  /** Omitted means always offered. */
  available?: (editor: Editor) => boolean;
}
