"use client";

import { InputTypeIn } from "@onyx-ai/opal/components";
import { SvgBubbleText, SvgFolder } from "@onyx-ai/opal/icons";
import { useRouter } from "next/navigation";
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { createPortal } from "react-dom";

import { apiFetch, ApiError } from "@/lib/api";
import { wikiHref, wikiPath } from "@/lib/wikiHref";

// Imperative handle so the sidebar can focus the search input after
// expanding from a collapsed state (the input only mounts when the
// sidebar is expanded).
export interface WikiSearchHandle {
  focus: () => void;
}

interface SearchHit {
  doc_id: string;
  path: string;
  title: string | null;
  snippet: string;
  score: number;
}

interface FolderHit {
  path: string;
}

interface CommentHit {
  comment_id: string;
  doc_path: string;
  thread_root_id: string;
  snippet: string;
  score: number;
}

interface SearchResponse {
  query: string;
  hits: SearchHit[];
  folders?: FolderHit[];
}

interface CommentSearchResponse {
  query: string;
  hits: CommentHit[];
}

type Row =
  | { kind: "folder"; folder: FolderHit }
  | { kind: "doc"; hit: SearchHit }
  | { kind: "comment"; hit: CommentHit };

const DEBOUNCE_MS = 150;
const RESULT_LIMIT = 10;
// Comments are secondary to docs in the dropdown — cap them lower so the
// combined list stays scannable.
const COMMENT_RESULT_LIMIT = 5;

interface WikiSearchProps {
  // Called after a result is picked — the sidebar uses this to collapse
  // the mobile drawer so the destination page is actually visible.
  onNavigate?: () => void;
}

export const WikiSearch = forwardRef<WikiSearchHandle, WikiSearchProps>(
  function WikiSearch({ onNavigate }, ref) {
    const router = useRouter();
    const [query, setQuery] = useState("");
    const [hits, setHits] = useState<SearchHit[]>([]);
    const [folders, setFolders] = useState<FolderHit[]>([]);
    const [comments, setComments] = useState<CommentHit[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [open, setOpen] = useState(false);
    const [activeIdx, setActiveIdx] = useState(0);
    const containerRef = useRef<HTMLDivElement>(null);
    const dropdownRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLInputElement>(null);
    // Viewport position of the dropdown — the sidebar nav clips overflow
    // (it needs overflow-hidden for its collapse animation), so the
    // dropdown renders in a body portal, fixed-positioned under the input.
    const [anchor, setAnchor] = useState<{ top: number; left: number } | null>(
      null,
    );
    // Track the latest in-flight request so a slower response can't overwrite
    // a fresher one's results.
    const requestSeq = useRef(0);

    useImperativeHandle(
      ref,
      () => ({
        focus: () => inputRef.current?.focus(),
      }),
      [],
    );

    useEffect(() => {
      const trimmed = query.trim();
      if (!trimmed) {
        setHits([]);
        setFolders([]);
        setComments([]);
        setLoading(false);
        setError(null);
        return;
      }
      const seq = ++requestSeq.current;
      setLoading(true);
      setError(null);
      const handle = setTimeout(() => {
        apiFetch<SearchResponse>(
          `/wiki/search?q=${encodeURIComponent(trimmed)}&limit=${RESULT_LIMIT}`,
        )
          .then((r) => {
            if (seq !== requestSeq.current) return;
            setHits(r.hits);
            setFolders(r.folders ?? []);
            setActiveIdx(0);
          })
          .catch((e) => {
            if (seq !== requestSeq.current) return;
            setHits([]);
            setFolders([]);
            setError(e instanceof ApiError ? e.message : "search failed");
          })
          .finally(() => {
            if (seq === requestSeq.current) setLoading(false);
          });
        // Comment search runs alongside docs and is best-effort chrome — a
        // failure (e.g. search backend down) silently yields no comment rows
        // rather than surfacing an error over the doc results.
        apiFetch<CommentSearchResponse>(
          `/comments/search?q=${encodeURIComponent(trimmed)}&limit=${COMMENT_RESULT_LIMIT}`,
        )
          .then((r) => {
            if (seq === requestSeq.current) setComments(r.hits);
          })
          .catch(() => {
            if (seq === requestSeq.current) setComments([]);
          });
      }, DEBOUNCE_MS);
      return () => clearTimeout(handle);
    }, [query]);

    // Close the dropdown on outside click. The dropdown lives in a body
    // portal, so "inside" means the input container or the dropdown itself.
    useEffect(() => {
      if (!open) return;
      function handleClick(e: MouseEvent) {
        const t = e.target as Node;
        if (containerRef.current?.contains(t)) return;
        if (dropdownRef.current?.contains(t)) return;
        setOpen(false);
      }
      window.addEventListener("mousedown", handleClick);
      return () => window.removeEventListener("mousedown", handleClick);
    }, [open]);

    // Folders first, then docs, then comments; all groups share one keyboard
    // cursor.
    const rows = useMemo<Row[]>(
      () => [
        ...folders.map<Row>((f) => ({ kind: "folder", folder: f })),
        ...hits.map<Row>((h) => ({ kind: "doc", hit: h })),
        ...comments.map<Row>((h) => ({ kind: "comment", hit: h })),
      ],
      [folders, hits, comments],
    );

    const pick = useCallback(
      (row: Row) => {
        setOpen(false);
        setQuery("");
        if (row.kind === "comment") {
          // Deep-link to the thread via its root id (the page handler matches
          // `?comment=` against thread roots; a matched reply resolves to its
          // root). Reuses the shipped click-to-focus/share-link route.
          router.push(
            `/app/wiki/${row.hit.doc_path}?comment=${row.hit.thread_root_id}`,
          );
        } else if (row.kind === "folder") {
          // no id in payload — folder hits stay path-based.
          router.push(wikiPath(row.folder.path));
        } else {
          router.push(wikiHref(row.hit.doc_id));
        }
        onNavigate?.();
      },
      [router, onNavigate],
    );

    function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
      if (!open || rows.length === 0) {
        if (e.key === "Escape") setOpen(false);
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIdx((i) => (i + 1) % rows.length);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIdx((i) => (i - 1 + rows.length) % rows.length);
      } else if (e.key === "Enter") {
        e.preventDefault();
        const row = rows[activeIdx];
        if (row) pick(row);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    }

    const showDropdown = open && query.trim().length > 0;
    const showEmpty = useMemo(
      () => showDropdown && !loading && !error && rows.length === 0,
      [showDropdown, loading, error, rows.length],
    );

    // Anchor the portal dropdown under the input; re-measure on viewport
    // resize and on any scroll (capture phase catches the sidebar's own
    // scroll containers, not just the window).
    useLayoutEffect(() => {
      if (!showDropdown) {
        setAnchor(null);
        return;
      }
      function measure() {
        const rect = containerRef.current?.getBoundingClientRect();
        if (rect) setAnchor({ top: rect.bottom + 4, left: rect.left });
      }
      measure();
      window.addEventListener("resize", measure);
      window.addEventListener("scroll", measure, true);
      return () => {
        window.removeEventListener("resize", measure);
        window.removeEventListener("scroll", measure, true);
      };
    }, [showDropdown]);

    return (
      <div ref={containerRef} className="relative w-full">
        <InputTypeIn
          ref={inputRef}
          variant="internal"
          searchIcon
          clearButton
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder="Search…"
          aria-label="Search wiki"
        />

        {showDropdown &&
          anchor &&
          createPortal(
            <div
              ref={dropdownRef}
              role="listbox"
              className="fixed z-[120] w-[360px] overflow-y-auto rounded-(--border-radius-08) border border-(--border-02) bg-(--background-tint-00) shadow-(--shadow-modal)"
              // Anchor-derived geometry stays inline — it's measured at runtime,
              // not a design token. z-[120] must stack above the OPAL SidebarTab
              // hit-target anchor (absolute inset-0 z-99) on the nav tabs below
              // the search box — anything lower and the invisible anchor swallows
              // clicks on dropdown rows that overlap a tab.
              style={{
                top: anchor.top,
                left: anchor.left,
                maxWidth: `calc(100vw - ${anchor.left}px - 12px)`,
                maxHeight: `calc(100vh - ${anchor.top}px - 12px)`,
              }}
            >
              {error && (
                <div className="p-3 text-[13px] text-(--status-text-error-05)">
                  {error}
                </div>
              )}
              {!error && loading && rows.length === 0 && (
                <div className="p-3 text-[13px] text-(--text-03)">
                  Searching…
                </div>
              )}
              {showEmpty && (
                <div className="p-3 text-[13px] text-(--text-03)">
                  No matches.
                </div>
              )}
              {!error && rows.length > 0 && (
                <ul className="m-0 list-none p-0">
                  {rows.map((row, i) => {
                    const active = i === activeIdx;
                    const key =
                      row.kind === "folder"
                        ? `f:${row.folder.path}`
                        : row.kind === "doc"
                          ? `d:${row.hit.doc_id}`
                          : `c:${row.hit.comment_id}`;
                    return (
                      <li key={key}>
                        <button
                          type="button"
                          onMouseEnter={() => setActiveIdx(i)}
                          onMouseDown={(e) => {
                            // mousedown so the input doesn't blur first.
                            e.preventDefault();
                            pick(row);
                          }}
                          className={`block w-full cursor-pointer border-b border-none border-(--border-01) px-3 py-2.5 text-left ${active ? "bg-(--background-tint-03)" : "bg-transparent"}`}
                        >
                          {row.kind === "folder" ? (
                            <FolderRow folder={row.folder} />
                          ) : row.kind === "doc" ? (
                            <DocRow hit={row.hit} />
                          ) : (
                            <CommentRow hit={row.hit} />
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>,
            document.body,
          )}
      </div>
    );
  },
);

function FolderRow({ folder }: { folder: FolderHit }) {
  const leaf = folder.path.split("/").pop() || folder.path;
  const parent = folder.path.includes("/")
    ? folder.path.slice(0, folder.path.lastIndexOf("/"))
    : "";
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className="flex shrink-0 text-(--text-03)">
        <SvgFolder size={16} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="overflow-hidden text-[13px] font-semibold text-ellipsis whitespace-nowrap text-(--text-05)">
          {leaf}
        </div>
        {parent && (
          <div className="mt-0.5 overflow-hidden font-mono text-[11px] text-ellipsis whitespace-nowrap text-(--text-03)">
            {parent}/
          </div>
        )}
      </div>
    </div>
  );
}

function DocRow({ hit }: { hit: SearchHit }) {
  return (
    <>
      <div className="overflow-hidden text-[13px] font-semibold text-ellipsis whitespace-nowrap text-(--text-05)">
        {hit.title || hit.path}
      </div>
      <div className="mt-0.5 overflow-hidden font-mono text-[11px] text-ellipsis whitespace-nowrap text-(--text-03)">
        {hit.path}
      </div>
      {hit.snippet && (
        <div className="mt-1 line-clamp-2 text-xs leading-[1.4] text-(--text-04)">
          <SnippetText text={hit.snippet} />
        </div>
      )}
    </>
  );
}

function CommentRow({ hit }: { hit: CommentHit }) {
  const leaf = hit.doc_path.split("/").pop() || hit.doc_path;
  return (
    <div className="flex min-w-0 items-start gap-2">
      <span className="mt-0.5 flex shrink-0 text-(--text-03)">
        <SvgBubbleText size={16} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="overflow-hidden text-[13px] text-ellipsis whitespace-nowrap text-(--text-04)">
          Comment in{" "}
          <span className="font-semibold text-(--text-05)">{leaf}</span>
        </div>
        {hit.snippet && (
          <div className="mt-1 line-clamp-2 text-xs leading-[1.4] text-(--text-04)">
            <SnippetText text={hit.snippet} />
          </div>
        )}
      </div>
    </div>
  );
}

// Snippets come back with highlight markers around hit terms — <em>…</em>
// from the OpenSearch highlighter, **…** from older/seed data. Render both
// as bold spans without going through a full markdown pipeline.
function SnippetText({ text }: { text: string }) {
  // The markers can nest (e.g. **bold <em>match</em>**) — strip any
  // leftover <em> tags inside a segment rather than rendering them.
  const stripEm = (s: string) => s.replace(/<\/?em>/g, "");
  const parts = text.split(/(\*\*[^*]+\*\*|<em>[^<]*<\/em>)/g);
  return (
    <>
      {parts.map((p, i) => {
        const inner = p.startsWith("**")
          ? p.slice(2, -2)
          : p.startsWith("<em>")
            ? p.slice(4, -5)
            : null;
        if (inner !== null) {
          return (
            <strong key={i} className="font-bold text-(--text-05)">
              {stripEm(inner)}
            </strong>
          );
        }
        return <span key={i}>{stripEm(p)}</span>;
      })}
    </>
  );
}
