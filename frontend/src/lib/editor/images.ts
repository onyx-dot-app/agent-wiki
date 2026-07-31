/** Editor-side image support: an upload plugin (paste/drop a file, hold a
 * placeholder, swap in the node) and a resizable NodeView for `blocks.ts`'s
 * `image` node.
 *
 * Sizing and the same-origin rule live in `media.ts`, shared with the media
 * types that come next.
 *
 * NodeView DOM stays React-free, so it is plain DOM pulling Opal tokens through
 * the classes in `src/app/css/editor.css`.
 */
import { Extension } from "@tiptap/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { EditorState, Plugin, PluginKey } from "@tiptap/pm/state";
import { insertPoint } from "@tiptap/pm/transform";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import type { EditorView, NodeView } from "@tiptap/pm/view";
import { ApiError, apiUpload } from "@/lib/api";
import { toast } from "@/hooks/useToast";
import {
  MAX_UPLOAD_BYTES,
  MAX_UPLOAD_LABEL,
  isSameOriginSrc,
  parseMediaWidth,
  srcWithoutFragment,
  withMediaWidth,
} from "./media";

/** Per-view uploader, registered by the upload plugin so surfaces outside it
 * (the slash menu's Image entry) reach the same upload path instead of
 * duplicating it. Absent when the view has no `pagePath` and so cannot upload. */
const uploaders = new WeakMap<EditorView, (file: File) => void>();

/** Open the OS file picker and upload the chosen images at the caret. No-op
 * on a view that cannot upload, which is also why the menu entry hides. */
export function promptImageUpload(view: EditorView): void {
  const upload = uploaders.get(view);
  if (!upload) return;
  // raw-ok: the OS file dialog is only reachable by clicking a file input.
  // Opal ships no equivalent, and this element is never rendered.
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/*";
  input.multiple = true;
  input.addEventListener("change", () => {
    for (const file of Array.from(input.files ?? [])) upload(file);
  });
  input.click();
}

/** True when this view can upload, so a caller can hide an entry that would
 * do nothing. */
export function canUploadImages(view: EditorView): boolean {
  return uploaders.has(view);
}

/** Width an inserted image lands on, in px. A cap, so a small icon is never
 * upscaled and a screenshot arrives readable rather than filling the column. */
const INSERT_MAX_WIDTH = 225;

/** Natural width of an image file, or null when it cannot be decoded (a
 * corrupt or unsupported file still uploads and the server decides). */
async function naturalWidthOf(file: File): Promise<number | null> {
  try {
    const bitmap = await createImageBitmap(file);
    const width = bitmap.width;
    bitmap.close();
    return width || null;
  } catch {
    return null;
  }
}

/** Smallest width a resize can land on, in px. */
const MIN_IMAGE_WIDTH = 80;

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(value, Math.max(min, max)));
}

/** Renders an `image` node as a token-styled wrapper + img, with corner and
 * edge drag handles shown only while the node is selected and the view is
 * editable. A drag previews width live on the img and commits one
 * `src`-rewriting transaction on release. A load failure swaps to a
 * placeholder box. */
class ImageNodeView implements NodeView {
  dom: HTMLElement;
  private img: HTMLImageElement;
  private handles: HTMLElement[] = [];
  private broken: HTMLElement | null = null;
  private node: PMNode;
  private view: EditorView;
  private getPos: () => number | undefined;
  private selected = false;
  private loadedBase: string | null = null;
  private cleanupDrag: (() => void) | null = null;

  constructor(
    node: PMNode,
    view: EditorView,
    getPos: () => number | undefined,
  ) {
    this.node = node;
    this.view = view;
    this.getPos = getPos;

    const wrapper = document.createElement("span");
    wrapper.className = "editor-image";
    this.dom = wrapper;

    const img = document.createElement("img");
    img.className = "editor-image-img";
    // The whole node drags as a unit via ProseMirror's own draggable handling.
    // The img must not start its own native image drag on top of that.
    img.draggable = false;
    img.addEventListener("error", this.onImgError);
    this.img = img;
    wrapper.appendChild(img);

    for (const kind of ["e", "se"] as const) {
      const handle = document.createElement("span");
      handle.className = `editor-image-handle editor-image-handle-${kind}`;
      handle.addEventListener("pointerdown", this.onHandleDown);
      this.handles.push(handle);
      wrapper.appendChild(handle);
    }

    this.applyAttrs();
  }

  private applyAttrs(): void {
    const src = this.node.attrs.src as string;
    this.img.alt = (this.node.attrs.alt as string) ?? "";
    const title = this.node.attrs.title as string | null;
    if (title) {
      this.img.title = title;
    } else {
      this.img.removeAttribute("title");
    }
    const nextBase = srcWithoutFragment(src);
    if (this.loadedBase !== nextBase) {
      this.loadedBase = nextBase;
      this.clearBroken();
      // Enforced here because this is where a src becomes a request, and a
      // node's attrs can arrive from any collaborator or from page markdown.
      if (isSameOriginSrc(nextBase)) {
        this.img.src = nextBase;
      } else {
        this.img.removeAttribute("src");
        this.markBroken("External image blocked");
      }
    }
    const width = parseMediaWidth(src);
    if (width != null) this.img.style.width = `${width}px`;
    else this.img.style.removeProperty("width");
  }

  private clearBroken(): void {
    this.dom.classList.remove("is-broken");
    this.broken?.remove();
    this.broken = null;
  }

  private markBroken(message: string): void {
    this.dom.classList.add("is-broken");
    if (this.broken) return;
    const box = document.createElement("span");
    box.className = "editor-image-broken";
    box.textContent = message;
    this.broken = box;
    this.dom.appendChild(box);
  }

  private onImgError = (): void => {
    this.markBroken("Image failed to load");
  };

  private columnWidth(): number {
    return this.view.dom.clientWidth || MIN_IMAGE_WIDTH;
  }

  private syncSelectionUi(): void {
    this.dom.classList.toggle(
      "is-resizable",
      this.selected && this.view.editable,
    );
  }

  private onHandleDown = (event: PointerEvent): void => {
    if (!this.view.editable) return;
    // Keep the resize gesture out of ProseMirror's selection/drag handling.
    event.preventDefault();
    event.stopPropagation();
    const handle = event.currentTarget as HTMLElement;
    handle.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startWidth = this.img.getBoundingClientRect().width;
    const maxWidth = this.columnWidth();
    let preview = startWidth;

    const onMove = (moveEvent: PointerEvent): void => {
      preview = clamp(
        startWidth + (moveEvent.clientX - startX),
        MIN_IMAGE_WIDTH,
        maxWidth,
      );
      this.img.style.width = `${Math.round(preview)}px`;
    };
    const onUp = (): void => {
      this.cleanupDrag?.();
      this.commitWidth(Math.round(preview));
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
    this.cleanupDrag = () => {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onUp);
      try {
        handle.releasePointerCapture(event.pointerId);
      } catch {
        // pointer capture was already released - nothing to undo
      }
      this.cleanupDrag = null;
    };
  };

  private commitWidth(width: number): void {
    const pos = this.getPos();
    if (typeof pos !== "number") return;
    const current = this.node.attrs.src as string;
    const next = withMediaWidth(current, width);
    if (next === current) return;
    this.view.dispatch(this.view.state.tr.setNodeAttribute(pos, "src", next));
  }

  update(node: PMNode): boolean {
    if (node.type.name !== "image") return false;
    this.node = node;
    this.applyAttrs();
    this.syncSelectionUi();
    return true;
  }

  selectNode(): void {
    this.selected = true;
    this.dom.classList.add("ProseMirror-selectednode");
    this.syncSelectionUi();
  }

  deselectNode(): void {
    this.selected = false;
    this.dom.classList.remove("ProseMirror-selectednode");
    this.syncSelectionUi();
  }

  stopEvent(event: Event): boolean {
    const target = event.target as HTMLElement | null;
    return (
      target != null &&
      this.handles.some((h) => h === target || h.contains(target))
    );
  }

  ignoreMutation(): boolean {
    // Leaf atom with no editable content: the handle/class DOM churn here is
    // all view-only, never something ProseMirror should read back as content.
    return true;
  }

  destroy(): void {
    this.cleanupDrag?.();
    this.img.removeEventListener("error", this.onImgError);
  }
}

/** Registers the `image` NodeView as a raw ProseMirror plugin prop, the
 * same `new Plugin(...)` idiom every other construct in this editor uses.
 * Registered unconditionally (unlike the upload plugin) so images render
 * for read-only viewers too. */
function imageNodeViewPlugin(): Plugin {
  return new Plugin({
    props: {
      nodeViews: {
        image: (node, view, getPos) => new ImageNodeView(node, view, getPos),
      },
    },
  });
}

interface UploadResponse {
  id: string;
  url: string;
  markdown: string;
}

interface UploadAction {
  add?: { id: object; pos: number };
  remove?: { id: object };
}

function imageFilesFrom(data: DataTransfer | null): File[] {
  if (!data) return [];
  return Array.from(data.files).filter((file) =>
    file.type.startsWith("image/"),
  );
}

/** Whether a drag carries files. During dragover the drag data store is in
 * protected mode, so the files themselves are unreadable and the type list is
 * the only signal available for deciding to claim the drop. */
function dragCarriesFiles(data: DataTransfer | null): boolean {
  return !!data && Array.from(data.types).includes("Files");
}

function findPlaceholder(
  key: PluginKey<DecorationSet>,
  state: EditorState,
  id: object,
): number | null {
  const set = key.getState(state);
  if (!set) return null;
  const found = set.find(undefined, undefined, (spec) => spec.id === id);
  return found.length > 0 ? found[0]!.from : null;
}

/** Paste/drop-to-upload for image files. A placeholder widget decoration
 * (keyed by a per-upload identity token, remapped through edits like the
 * highlight plugins' decorations) holds the spot while the bytes upload.
 * Success swaps it for an `image` node, failure removes it and surfaces the
 * reason through the app toast. */
function imageUploadPlugin(pagePath: string): Plugin<DecorationSet> {
  const key = new PluginKey<DecorationSet>("imageUpload");

  const startUpload = (view: EditorView, file: File, rawPos: number): void => {
    const imageType = view.state.schema.nodes.image;
    if (!imageType) return;
    // Refused before the bytes leave the browser: a proxy rejects an oversized
    // body first, and its reply carries no message worth showing.
    if (file.size > MAX_UPLOAD_BYTES) {
      toast.error(`${file.name} is larger than ${MAX_UPLOAD_LABEL}.`);
      return;
    }
    // Land the placeholder (and later the node) at the nearest position an
    // inline image is actually allowed, so a drop at a block boundary still
    // produces a valid document.
    const point = insertPoint(view.state.doc, rawPos, imageType);
    if (point == null) return;
    const id = {}; // identity token matched back to its decoration by reference
    view.dispatch(view.state.tr.setMeta(key, { add: { id, pos: point } }));

    const query = `path=${encodeURIComponent(pagePath)}&filename=${encodeURIComponent(file.name)}`;
    // Navigating away destroys the view mid-upload, and dispatching on a
    // destroyed view throws. Split handlers keep a failed insert off the toast.
    Promise.all([
      apiUpload<UploadResponse>(`/wiki/media?${query}`, file, file.type),
      naturalWidthOf(file),
    ]).then(
      ([res, natural]) => {
        if (view.isDestroyed) return;
        const at = findPlaceholder(key, view.state, id);
        if (at == null) return; // the target was deleted mid-upload - drop it
        const width = Math.min(natural ?? INSERT_MAX_WIDTH, INSERT_MAX_WIDTH);
        const node = imageType.create({
          src: withMediaWidth(res.url, width),
          alt: file.name,
          title: null,
        });
        view.dispatch(
          view.state.tr
            .replaceWith(at, at, node)
            .setMeta(key, { remove: { id } }),
        );
      },
      (err: unknown) => {
        if (view.isDestroyed) return;
        view.dispatch(view.state.tr.setMeta(key, { remove: { id } }));
        toast.error(
          err instanceof ApiError ? err.message : "Image upload failed",
        );
      },
    );
  };

  return new Plugin<DecorationSet>({
    key,
    // handleDrop only sees the contenteditable, but the wrapper centres a
    // capped column, so a drop in the gutters either side falls through to
    // the browser and navigates away. These listeners claim that region.
    view(editorView) {
      uploaders.set(editorView, (file) =>
        startUpload(editorView, file, editorView.state.selection.from),
      );
      // Resolved on a later tick: this runs while the EditorView is being
      // constructed, before its dom is inside the scroll wrapper, so an
      // eager closest() finds nothing.
      let scroller: Element | null = null;
      const outside = (event: DragEvent) =>
        !editorView.dom.contains(event.target as Node | null);
      // Without a prevented dragover the browser never offers the region as a
      // drop target, which is what makes the drop navigate instead.
      const onDragOver = (event: DragEvent) => {
        if (!editorView.editable || !outside(event)) return;
        if (!dragCarriesFiles(event.dataTransfer)) return;
        event.preventDefault();
      };
      const onDrop = (event: DragEvent) => {
        if (!editorView.editable || !outside(event)) return;
        const files = imageFilesFrom(event.dataTransfer);
        if (files.length === 0) return;
        event.preventDefault();
        // A gutter drop has no text under it, and the doc's end is not a
        // position an inline image is allowed at, so fall back to the caret
        // the way paste does.
        const coords = editorView.posAtCoords({
          left: event.clientX,
          top: event.clientY,
        });
        const at = coords?.pos ?? editorView.state.selection.from;
        for (const file of files) startUpload(editorView, file, at);
      };
      const attach = () => {
        scroller = editorView.dom.closest(".editor-prose");
        if (!scroller) return;
        scroller.addEventListener("dragover", onDragOver as EventListener);
        scroller.addEventListener("drop", onDrop as EventListener);
      };
      const attachTimer = window.setTimeout(attach, 0);
      return {
        destroy() {
          window.clearTimeout(attachTimer);
          uploaders.delete(editorView);
          scroller?.removeEventListener(
            "dragover",
            onDragOver as EventListener,
          );
          scroller?.removeEventListener("drop", onDrop as EventListener);
        },
      };
    },
    state: {
      init: () => DecorationSet.empty,
      apply(tr, set) {
        set = set.map(tr.mapping, tr.doc);
        const action = tr.getMeta(key) as UploadAction | undefined;
        if (action?.add) {
          const widget = document.createElement("span");
          widget.className = "editor-image-uploading";
          set = set.add(tr.doc, [
            Decoration.widget(action.add.pos, widget, { id: action.add.id }),
          ]);
        }
        if (action?.remove) {
          const removeId = action.remove.id;
          set = set.remove(
            set.find(undefined, undefined, (spec) => spec.id === removeId),
          );
        }
        return set;
      },
    },
    props: {
      decorations(state) {
        return key.getState(state);
      },
      handlePaste(view, event) {
        if (!view.editable) return false; // a viewer's paste never uploads
        const files = imageFilesFrom(event.clipboardData);
        if (files.length === 0) return false;
        event.preventDefault();
        const at = view.state.selection.from;
        for (const file of files) startUpload(view, file, at);
        return true;
      },
      handleDrop(view, event, _slice, moved) {
        if (!view.editable) return false; // a viewer's drop never uploads
        if (moved) return false; // an internal node move, not a file drop
        const files = imageFilesFrom(event.dataTransfer);
        if (files.length === 0) return false;
        event.preventDefault();
        const coords = view.posAtCoords({
          left: event.clientX,
          top: event.clientY,
        });
        const at = coords?.pos ?? view.state.selection.from;
        for (const file of files) startUpload(view, file, at);
        return true;
      },
    },
  });
}

/** All editor-side image behavior in one extension, mirroring
 * `presenceExtension(awareness)`'s factory shape. The NodeView is always
 * active so images render for read-only viewers. The paste/drop upload
 * plugin is added only when a `pagePath` is known (the upload endpoint is
 * page-scoped), so the scaffold/verification harness with no real page just
 * lets an image paste fall through to default handling. */
export function imageSupport(pagePath: string | undefined): Extension {
  return Extension.create({
    name: "imageSupport",
    addProseMirrorPlugins() {
      const plugins: Plugin[] = [imageNodeViewPlugin()];
      if (pagePath) plugins.push(imageUploadPlugin(pagePath));
      return plugins;
    },
  });
}
