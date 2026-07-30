"use client";

import { useCallback, useEffect, useRef, type RefObject } from "react";

import type { CoeditorHandle } from "@/lib/editor/types";

/**
 * Doc scrollbar at the viewport's right edge for the panel's anchored mode,
 * whose hidden native bar would sit at the doc/panel boundary. Direct style
 * writes on scroll notifications, pointer-capture drag.
 */
export function EditorEdgeScrollbar({
  editorRef,
}: {
  editorRef: RefObject<CoeditorHandle | null>;
}) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const thumbRef = useRef<HTMLDivElement | null>(null);
  const drag = useRef<{ startY: number; startTop: number } | null>(null);

  const metrics = useCallback(() => {
    const editor = editorRef.current;
    const track = trackRef.current;
    if (!editor || !track) return null;
    const sh = editor.scrollHeight();
    const ch = editor.clientHeight();
    const trackH = track.clientHeight;
    if (sh <= ch + 1 || trackH <= 0) return null;
    const thumbH = Math.max(24, (ch / sh) * trackH);
    return { sh, ch, trackH, thumbH, maxThumb: trackH - thumbH };
  }, [editorRef]);

  const sync = useCallback(() => {
    const editor = editorRef.current;
    const thumb = thumbRef.current;
    if (!editor || !thumb) return;
    const m = metrics();
    if (!m) {
      thumb.style.display = "none";
      return;
    }
    thumb.style.display = "";
    thumb.style.height = `${m.thumbH}px`;
    const ratio = editor.scrollTop() / (m.sh - m.ch);
    thumb.style.transform = `translateY(${ratio * m.maxThumb}px)`;
  }, [editorRef, metrics]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    sync();
    return editor.subscribeLayout(sync);
  }, [editorRef, sync]);

  return (
    /* raw-ok: a custom scrollbar control has no Opal equivalent */
    <div
      ref={trackRef}
      className="absolute inset-y-1 -right-2 w-3"
      onPointerDown={(e) => {
        const editor = editorRef.current;
        const thumb = thumbRef.current;
        const m = metrics();
        if (!editor || !thumb || !m) return;
        const thumbRect = thumb.getBoundingClientRect();
        if (e.clientY < thumbRect.top || e.clientY > thumbRect.bottom) {
          // Track press: jump so the thumb centers on the pointer.
          const trackTop = trackRef.current!.getBoundingClientRect().top;
          const ratio = Math.min(
            1,
            Math.max(0, (e.clientY - trackTop - m.thumbH / 2) / m.maxThumb),
          );
          editor.scrollBy(ratio * (m.sh - m.ch) - editor.scrollTop());
        }
        drag.current = { startY: e.clientY, startTop: editor.scrollTop() };
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        const editor = editorRef.current;
        const m = metrics();
        if (!drag.current || !editor || !m) return;
        const dy = e.clientY - drag.current.startY;
        const next = drag.current.startTop + (dy / m.maxThumb) * (m.sh - m.ch);
        editor.scrollBy(next - editor.scrollTop());
      }}
      onPointerUp={(e) => {
        drag.current = null;
        e.currentTarget.releasePointerCapture(e.pointerId);
      }}
    >
      <div
        ref={thumbRef}
        className="mx-auto w-[6px] rounded-full bg-(--border-03) hover:bg-(--text-04)"
      />
    </div>
  );
}
