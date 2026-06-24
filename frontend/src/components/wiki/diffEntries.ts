import type { DiffHunk, DiffLine } from "@/lib/wiki";

/* Grouping + change-indexing for the diff viewer. Coalesces consecutive
   same-kind lines into render blocks, then tags the first entry of each
   contiguous run of changes with a sequential index the navigator steps
   through. Kept pure (no React) so it's trivially testable. */

/** Every diff-line kind except the synthetic single-line "word" edit, derived
    from the source type so it can't drift from DiffLine. */
export type BlockKind = Exclude<DiffLine["kind"], "word">;

export interface BlockEntry {
  kind: BlockKind;
  text: string;
}

export interface WordEntry {
  kind: "word";
  line: DiffLine;
}

export type Entry = BlockEntry | WordEntry;

export interface AnnotatedEntry {
  entry: Entry;
  /** Set when this entry begins a contiguous change run; null otherwise.
      It's the navigator's scroll target index for that change. */
  changeIndex: number | null;
}

export function groupEntries(lines: DiffLine[]): Entry[] {
  const out: Entry[] = [];
  let current: BlockEntry | null = null;
  const flush = () => {
    if (current) {
      out.push(current);
      current = null;
    }
  };
  for (const line of lines) {
    if (line.kind === "word") {
      flush();
      out.push({ kind: "word", line });
      continue;
    }
    const text = line.text ?? "";
    if (current && current.kind === line.kind) {
      current.text += "\n" + text;
    } else {
      flush();
      current = { kind: line.kind, text };
    }
  }
  flush();
  return out;
}

/** Group every hunk and number the changes. Each change entry is its own
    change, EXCEPT an add block directly after a remove block — that's the
    "new" half of a replacement, so it stays part of the remove's change.
    The change index marks the entry the navigator scrolls to. */
export function annotateHunks(hunks: DiffHunk[]): {
  perHunk: AnnotatedEntry[][];
  total: number;
} {
  let changeIndex = 0;
  const perHunk = hunks.map((hunk) => {
    let prevKind: Entry["kind"] = "context";
    return groupEntries(hunk.lines).map((entry) => {
      const isChange = entry.kind !== "context";
      const continuesReplacement =
        entry.kind === "add" && prevKind === "remove";
      const startsChange = isChange && !continuesReplacement;
      prevKind = entry.kind;
      return { entry, changeIndex: startsChange ? changeIndex++ : null };
    });
  });
  return { perHunk, total: changeIndex };
}
