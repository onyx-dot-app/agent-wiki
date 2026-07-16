import { SelectButton, Text } from "@onyx-ai/opal/components";
import { SvgChevronDown, SvgChevronUp } from "@onyx-ai/opal/icons";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { remarkBareSpaceLinks } from "@/lib/remarkBareSpaceLinks";

import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { absoluteTime, relativeTime } from "@/lib/time";
import type { FileDiffResponse } from "@/lib/wiki/types";

import { DiffHunk } from "./DiffHunk";
import { annotateHunks } from "./diffEntries";
import styles from "./DiffView.module.css";

interface CommitMeta {
  sha: string;
  message: string;
  author: string;
  ts: string;
}

type Mode = "diff" | "doc";

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
        <button
          type="button"
          className={styles.navBtn}
          onClick={onPrev}
          aria-label="Previous change"
        >
          <SvgChevronUp size={16} />
        </button>
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

export function DiffView({
  data,
  commit,
  loadBody,
}: {
  data: FileDiffResponse;
  commit: CommitMeta | undefined;
  loadBody: () => Promise<string>;
}) {
  const [mode, setMode] = useState<Mode>("diff");
  const [body, setBody] = useState<string | null>(null);
  const [bodyError, setBodyError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [current, setCurrent] = useState(0);
  const [navTop, setNavTop] = useState<number | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<HTMLDivElement>(null);
  const currentRef = useRef(0);

  const { perHunk, total } = useMemo(() => annotateHunks(data.hunks), [data]);

  const shaShort = data.sha.slice(0, 7);
  const authorLabel = commit?.author ?? "?";
  const timeLabel = commit?.ts ? relativeTime(commit.ts, "long") : null;
  const metaPieces = [shaShort, authorLabel];
  if (timeLabel) metaPieces.push(timeLabel);
  const metaLine = metaPieces.join(" · ");
  const metaTitle = commit?.ts ? absoluteTime(commit.ts) : undefined;

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

  // New commit selected → back to diff mode at the first change.
  useEffect(() => {
    setMode("diff");
    setCurrent(0);
    currentRef.current = 0;
  }, [data]);

  // Once the diff for a new commit has painted, jump to the first change so a
  // commit click lands you on the change, not the top of the file.
  useLayoutEffect(() => {
    if (mode !== "diff") return;
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
  }, [data, mode, scrollToChange, updateNavPosition]);

  // Keep the navigator beside the active change while the diff is scrolled.
  useEffect(() => {
    const container = bodyRef.current;
    if (mode !== "diff" || !container) return;
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
  }, [data, mode, total, updateNavPosition]);

  async function pickMode(next: Mode) {
    if (next === mode) return;
    setMode(next);
    if (next === "doc" && body === null) {
      setLoading(true);
      setBodyError(null);
      try {
        const text = await loadBody();
        setBody(text);
      } catch (e) {
        setBodyError(e instanceof Error ? e.message : "failed to load doc");
      } finally {
        setLoading(false);
      }
    }
  }

  return (
    <div className={styles.view} ref={viewRef}>
      <div className={styles.header}>
        <div className={styles.headerInfo}>
          <Text
            font="main-ui-action"
            color="text-04"
            as="p"
            nowrap
            maxLines={1}
          >
            {commit?.message || "(no message)"}
          </Text>
          <Text
            font="secondary-body"
            color="text-03"
            as="p"
            nowrap
            maxLines={1}
            title={metaTitle}
          >
            {metaLine}
          </Text>
        </div>
        <div role="tablist" aria-label="View mode" className={styles.toggle}>
          <SelectButton
            size="sm"
            state={mode === "diff" ? "selected" : "empty"}
            onClick={() => pickMode("diff")}
          >
            Diff
          </SelectButton>
          <SelectButton
            size="sm"
            state={mode === "doc" ? "selected" : "empty"}
            onClick={() => void pickMode("doc")}
          >
            Doc
          </SelectButton>
        </div>
      </div>
      <div className={styles.body} ref={bodyRef}>
        {mode === "diff" ? (
          data.hunks.length === 0 ? (
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
          )
        ) : loading ? (
          <div className={styles.empty}>
            <LoadingSpinner />
          </div>
        ) : bodyError ? (
          <div className={styles.empty}>
            <Text font="secondary-body" color="text-03">
              {bodyError}
            </Text>
          </div>
        ) : (
          <article className={`${styles.doc} markdown`}>
            <ReactMarkdown remarkPlugins={[remarkGfm, remarkBareSpaceLinks]}>
              {body ?? ""}
            </ReactMarkdown>
          </article>
        )}
      </div>
      {mode === "diff" && total > 0 ? (
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
