/** `TabIndent` behaviors through a real headless Tiptap editor: a plain
 * line below a list (blank-line blocks between are skipped) indents into
 * it on Tab, everything else swallows Tab so the editor keeps focus. */
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
import { TabIndent } from "@/lib/editor/extensions/tabIndent";

function makeEditor(): Editor {
  return new Editor({
    extensions: [
      StarterKit.configure({ undoRedo: false, trailingNode: false }),
      MixedTaskList,
      TaskItem.configure({ nested: true }),
      BlockIdentity,
      JoinAdjacentLists,
      UniqueBlockIdentity,
      TabIndent,
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

function para(text?: string): Json {
  return text
    ? { type: "paragraph", content: [{ type: "text", text }] }
    : { type: "paragraph" };
}

function topLevelTypes(editor: Editor): string[] {
  const out: string[] = [];
  editor.state.doc.forEach((node) => out.push(node.type.name));
  return out;
}

function pressTab(editor: Editor): boolean {
  return editor.commands.keyboardShortcut("Tab");
}

function selectEndOfLastParagraph(editor: Editor) {
  // Place the caret inside the trailing paragraph's text.
  editor.commands.setTextSelection(editor.state.doc.content.size - 1);
}

describe("TabIndent", () => {
  it("indents a plain line directly below a bullet list into it", () => {
    const editor = makeEditor();
    editor.commands.setContent({
      type: "doc",
      content: [
        { type: "bulletList", content: [listItem("a"), listItem("b")] },
        para("target"),
      ],
    });
    selectEndOfLastParagraph(editor);
    expect(pressTab(editor)).toBe(true);
    // Joined into the single preceding list.
    expect(topLevelTypes(editor)).toEqual(["bulletList"]);
    expect(editor.state.doc.child(0).childCount).toBe(3);
  });

  it("skips blank-line blocks when looking back for the list", () => {
    const editor = makeEditor();
    editor.commands.setContent({
      type: "doc",
      content: [
        { type: "bulletList", content: [listItem("a")] },
        para(),
        para("target"),
      ],
    });
    selectEndOfLastParagraph(editor);
    expect(pressTab(editor)).toBe(true);
    // The blank stays; the target became a (separate) bullet list that a
    // markdown reparse reads as part of the same loose list.
    expect(topLevelTypes(editor)).toEqual([
      "bulletList",
      "paragraph",
      "bulletList",
    ]);
  });

  it("indents a plain line below a task list into it", () => {
    const editor = makeEditor();
    editor.commands.setContent({
      type: "doc",
      content: [
        { type: "taskList", content: [taskItem("a")] },
        para(),
        para("target"),
      ],
    });
    selectEndOfLastParagraph(editor);
    expect(pressTab(editor)).toBe(true);
    expect(topLevelTypes(editor)).toEqual([
      "taskList",
      "paragraph",
      "taskList",
    ]);
  });

  it("swallows Tab on a plain line with no list above", () => {
    const editor = makeEditor();
    editor.commands.setContent({
      type: "doc",
      content: [para("alpha"), para("target")],
    });
    selectEndOfLastParagraph(editor);
    expect(pressTab(editor)).toBe(true); // handled (swallowed), not focus-out
    expect(topLevelTypes(editor)).toEqual(["paragraph", "paragraph"]);
  });

  it("leaves Tab to the list handling inside a list", () => {
    const editor = makeEditor();
    editor.commands.setContent({
      type: "doc",
      content: [
        { type: "bulletList", content: [listItem("a"), listItem("b")] },
      ],
    });
    selectEndOfLastParagraph(editor);
    pressTab(editor);
    // The second item nested under the first — native sink behavior.
    const first = editor.state.doc.child(0).child(0);
    expect(first.lastChild?.type.name).toBe("bulletList");
  });
});
