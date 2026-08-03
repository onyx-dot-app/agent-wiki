import { Text } from "@onyx-ai/opal/components";
import { SvgChevronDown, SvgChevronUp } from "@onyx-ai/opal/icons";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type { FileDiffResponse } from "@/lib/wiki/types";
import {
  EdgeScrollbar,
  useElementScrollTarget,
} from "@/components/wiki/EdgeScrollbar";

import { DiffHunk } from "./DiffHunk";
import { annotateHunks } from "./diffEntries";
import styles from "./DiffView.module.css";

/** Floating pill (Figma 283:22157): change position + prev/next steppers. */
function ChangeNavigator({
  current,
  total,
  top,
  onPrev,
  onNext,
}: {
  current: number;
  total: number;
  top: number | null;
  onPrev: () => void;
  onNext: () => void;
}) {
  return (
    <div
      className={styles.navigator}
      style={top !== null ? { top: `${top}px` } : undefined}
    >
      <Text font="secondary-mono" color="text-03" nowrap>
        {`${current + 1} / ${total}`}
      </Text>
      <div className={styles.navArrows}>
        {/* raw-ok: pill steppers use the module's compact hit targets. Opal icon Buttons carry their own padding/radius that fight the pill geometry */}
        <button
          type="button"
          className={styles.navBtn}
          onClick={onPrev}
          aria-label="Previous change"
        >
          <SvgChevronUp size={16} />
        </button>
        {/* raw-ok: same pill stepper as above */}
        <button
          type="button"
          className={styles.navBtn}
          onClick={onNext}
          aria-label="Next change"
        >
          <SvgChevronDown size={16} />
        </button>
      </div>
    </div>
  );
}

export function DiffView({ data }: { data: FileDiffResponse }) {
  const [current, setCurrent] = useState(0);
  const [navTop, setNavTop] = useState<number | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<HTMLDivElement>(null);
  const scrollTarget = useElementScrollTarget(bodyRef);
  const currentRef = useRef(0);

  const { perHunk, total } = useMemo(() => annotateHunks(data.hunks), [data]);

  // Center change `i` in the scroll body. We set scrollTop directly rather
  // than scrollIntoView: smooth scrolling no-ops inside this nested overflow
  // container, and scrollIntoView there is unreliable.
  const scrollToChange = useCallback((i: number) => {
    const container = bodyRef.current;
    const el = container?.querySelector<HTMLElement>(
      `[data-change-index="${i}"]`,
    );
    if (!container || !el) return;
    const cRect = container.getBoundingClientRect();
    const eRect = el.getBoundingClientRect();
    const elTop = eRect.top - cRect.top + container.scrollTop;
    const target = elTop - (container.clientHeight - eRect.height) / 2;
    const max = container.scrollHeight - container.clientHeight;
    container.scrollTop = Math.max(0, Math.min(max, target));
  }, []);

  // Park the navigator vertically beside the current change, clamped inside
  // the view so it stays visible when the change scrolls toward an edge.
  const updateNavPosition = useCallback(() => {
    const view = viewRef.current;
    const el = bodyRef.current?.querySelector<HTMLElement>(
      `[data-change-index="${currentRef.current}"]`,
    );
    if (!view || !el) return;
    const vRect = view.getBoundingClientRect();
    const eRect = el.getBoundingClientRect();
    const center = eRect.top + eRect.height / 2 - vRect.top;
    const margin = 28;
    setNavTop(Math.max(margin, Math.min(vRect.height - margin, center)));
  }, []);

  const goToChange = useCallback(
    (i: number) => {
      if (total === 0) return;
      // Wrap around so next past the last change returns to the first.
      const wrapped = ((i % total) + total) % total;
      currentRef.current = wrapped;
      setCurrent(wrapped);
      scrollToChange(wrapped);
      updateNavPosition();
    },
    [total, scrollToChange, updateNavPosition],
  );

  // New commit selected → back to the first change.
  useEffect(() => {
    setCurrent(0);
    currentRef.current = 0;
  }, [data]);

  // Once the diff for a new commit has painted, jump to the first change so a
  // commit click lands you on the change, not the top of the file.
  useLayoutEffect(() => {
    let cancelled = false;
    const jump = () => {
      if (cancelled) return;
      scrollToChange(0);
      updateNavPosition();
    };
    const raf = requestAnimationFrame(jump);
    // Re-run once web fonts settle — their metrics shift line positions, so a
    // first-frame jump can land short on a freshly loaded, content-heavy diff.
    void document.fonts?.ready.then(() => requestAnimationFrame(jump));
    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
    };
  }, [data, scrollToChange, updateNavPosition]);

  // Keep the navigator beside the active change while the diff is scrolled.
  useEffect(() => {
    const container = bodyRef.current;
    if (!container) return;
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(updateNavPosition);
    };
    container.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      container.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(raf);
    };
  }, [data, total, updateNavPosition]);

  return (
    <div className={styles.view} ref={viewRef}>
      <div className={styles.body} ref={bodyRef}>
        {data.hunks.length === 0 ? (
          <div className={styles.empty}>
            <Text font="secondary-body" color="text-03">
              No changes for this file in that commit.
            </Text>
          </div>
        ) : (
          <div className={styles.diffContent}>
            {perHunk.map((entries, idx) => (
              <DiffHunk key={idx} entries={entries} />
            ))}
          </div>
        )}
      </div>
      {/* Same thumb as the live editor (the body's native bar is hidden in
          the module CSS) — one scroll indicator across doc surfaces. */}
      <EdgeScrollbar
        targetRef={scrollTarget}
        className="absolute inset-y-1 right-0 w-3"
      />
      {total > 0 ? (
        <ChangeNavigator
          current={current}
          total={total}
          top={navTop}
          onPrev={() => goToChange(current - 1)}
          onNext={() => goToChange(current + 1)}
        />
      ) : null}
    </div>
  );
}
