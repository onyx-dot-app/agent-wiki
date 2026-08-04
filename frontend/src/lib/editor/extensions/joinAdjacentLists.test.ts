/** `JoinAdjacentLists` invariants, exercised through a real (headless)
 * Tiptap editor so `appendTransaction` runs exactly as it does live.
 *
 * The stakes: a document holding two adjacent same-type lists disagrees
 * with every markdown parse of its own file (markdown reads them as one
 * list), and the checkpoint's block-id restamp + `UniqueBlockIdentity`'s
 * duplicate-clear turn that disagreement into duplicated file content on
 * every save. The join is what removes the shape; these tests pin the
 * behaviours a regression would need to break. */
import { Editor } from "@tiptap/core";
import { StarterKit } from "@tiptap/starter-kit";
import { TaskItem } from "@tiptap/extension-task-item";
import { describe, expect, it } from "vitest";
import {
  BlockIdentity,
  JoinAdjacentLists,
  MixedTaskList,
  UniqueBlockIdentity,
} from "@/lib/editor/extensions/blocks";

function makeEditor(): Editor {
  return new Editor({
    extensions: [
      // trailingNode off to match the production editor (extensions.ts) —
      // and because an auto-appended paragraph would pad every top-level
      // count these tests assert.
      StarterKit.configure({ undoRedo: false, trailingNode: false }),
      MixedTaskList,
      TaskItem.configure({ nested: true }),
      BlockIdentity,
      JoinAdjacentLists,
      UniqueBlockIdentity,
    ],
  });
}

type Json = Record<string, unknown>;

function taskItem(text: string): Json {
  return {
    type: "taskItem",
    attrs: { checked: false },
    content: [{ type: "paragraph", content: [{ type: "text", text }] }],
  };
}

function listItem(text: string): Json {
  return {
    type: "listItem",
    content: [{ type: "paragraph", content: [{ type: "text", text }] }],
  };
}

function topLevel(editor: Editor): { type: string; attrs: Json }[] {
  const out: { type: string; attrs: Json }[] = [];
  editor.state.doc.forEach((node) => {
    out.push({ type: node.type.name, attrs: node.attrs as Json });
  });
  return out;
}

describe("JoinAdjacentLists", () => {
  it("joins two adjacent task lists into one, keeping the first list's identity", () => {
    const editor = makeEditor();
    // The production shape: one file block modelled as two doc nodes, the
    // restamp having stamped the same id onto both.
    editor.commands.setContent({
      type: "doc",
      content: [
        {
          type: "taskList",
          attrs: { _blockId: "b10" },
          content: [taskItem("one"), taskItem("two")],
        },
        {
          type: "taskList",
          attrs: { _blockId: "b10" },
          content: [taskItem("three")],
        },
      ],
    });
    const blocks = topLevel(editor);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]!.type).toBe("taskList");
    // Join won before UniqueBlockIdentity ran: the merged node kept the
    // first list's id rather than the second half being cleared to null —
    // a cleared id is what the checkpoint reads as brand-new content.
    expect(blocks[0]!.attrs._blockId).toBe("b10");
    expect(editor.state.doc.firstChild!.childCount).toBe(3);
  });

  it("keeps the first list's attrs when starts differ", () => {
    const editor = makeEditor();
    editor.commands.setContent({
      type: "doc",
      content: [
        {
          type: "orderedList",
          attrs: { start: 3, tight: "true", _blockId: "b1" },
          content: [listItem("a")],
        },
        {
          type: "orderedList",
          attrs: { start: 9, _blockId: "b2" },
          content: [listItem("b")],
        },
      ],
    });
    const blocks = topLevel(editor);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]!.attrs.start).toBe(3);
    expect(blocks[0]!.attrs.tight).toBe("true");
    expect(blocks[0]!.attrs._blockId).toBe("b1");
  });

  it("converges a run of three lists in one pass", () => {
    const editor = makeEditor();
    editor.commands.setContent({
      type: "doc",
      content: [
        { type: "bulletList", content: [listItem("a")] },
        { type: "bulletList", content: [listItem("b")] },
        { type: "bulletList", content: [listItem("c")] },
      ],
    });
    const blocks = topLevel(editor);
    expect(blocks).toHaveLength(1);
    expect(editor.state.doc.firstChild!.childCount).toBe(3);
  });

  it("leaves different-type neighbours and separated lists alone", () => {
    const editor = makeEditor();
    editor.commands.setContent({
      type: "doc",
      content: [
        { type: "bulletList", content: [listItem("a")] },
        { type: "orderedList", attrs: { start: 1 }, content: [listItem("b")] },
        { type: "paragraph", content: [{ type: "text", text: "between" }] },
        { type: "orderedList", attrs: { start: 1 }, content: [listItem("c")] },
      ],
    });
    expect(topLevel(editor).map((b) => b.type)).toEqual([
      "bulletList",
      "orderedList",
      "paragraph",
      "orderedList",
    ]);
  });

  it("joins lists that become adjacent through a later edit", () => {
    const editor = makeEditor();
    editor.commands.setContent({
      type: "doc",
      content: [
        { type: "taskList", content: [taskItem("a")] },
        { type: "paragraph", content: [{ type: "text", text: "between" }] },
        { type: "taskList", content: [taskItem("b")] },
      ],
    });
    expect(topLevel(editor)).toHaveLength(3);
    // Delete the separating paragraph — the join must fire on the edit
    // that creates the adjacency, not only on load.
    const doc = editor.state.doc;
    const first = doc.firstChild!;
    const para = doc.child(1);
    const from = first.nodeSize;
    editor
      .chain()
      .deleteRange({ from, to: from + para.nodeSize })
      .run();
    const blocks = topLevel(editor);
    expect(blocks).toHaveLength(1);
    expect(editor.state.doc.firstChild!.childCount).toBe(2);
  });
});
