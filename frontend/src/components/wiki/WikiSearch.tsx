"use client";

import { InputTypeIn } from "@onyx-ai/opal/components";
import { SvgFolder } from "@onyx-ai/opal/icons";
import { useRouter } from "next/navigation";
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import { apiFetch, ApiError } from "@/lib/api";

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

interface SearchResponse {
  query: string;
  hits: SearchHit[];
  folders?: FolderHit[];
}

type Row =
  | { kind: "folder"; folder: FolderHit }
  | { kind: "doc"; hit: SearchHit };

const DEBOUNCE_MS = 150;
const RESULT_LIMIT = 10;

export const WikiSearch = forwardRef<WikiSearchHandle>(function WikiSearch(_, ref) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [folders, setFolders] = useState<FolderHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
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
    }, DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [query]);

  // Close the dropdown on outside click.
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    window.addEventListener("mousedown", handleClick);
    return () => window.removeEventListener("mousedown", handleClick);
  }, [open]);

  // Folders render first; both groups share one keyboard cursor.
  const rows = useMemo<Row[]>(
    () => [
      ...folders.map<Row>((f) => ({ kind: "folder", folder: f })),
      ...hits.map<Row>((h) => ({ kind: "doc", hit: h })),
    ],
    [folders, hits],
  );

  const pick = useCallback(
    (row: Row) => {
      setOpen(false);
      setQuery("");
      const path = row.kind === "folder" ? row.folder.path : row.hit.path;
      router.push(`/app/wiki/${path}`);
    },
    [router],
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

      {showDropdown && (
        <div
          role="listbox"
          className="absolute top-[calc(100%+4px)] left-0 min-w-full w-[360px] max-w-[70vw] bg-(--background-tint-00) border border-(--border-01) rounded-(--border-radius-08) shadow-(--shadow-popover) max-h-[420px] overflow-y-auto z-[40]"
        >
          {error && (
            <div className="p-3 text-[13px] text-(--status-text-error-05)">{error}</div>
          )}
          {!error && loading && rows.length === 0 && (
            <div className="p-3 text-[13px] text-(--text-03)">Searching…</div>
          )}
          {showEmpty && (
            <div className="p-3 text-[13px] text-(--text-03)">No matches.</div>
          )}
          {!error && rows.length > 0 && (
            <ul className="list-none m-0 p-0">
              {rows.map((row, i) => {
                const active = i === activeIdx;
                const key =
                  row.kind === "folder" ? `f:${row.folder.path}` : `d:${row.hit.doc_id}`;
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
                      className={`w-full text-left py-2.5 px-3 border-none cursor-pointer block border-b border-(--border-01) ${active ? "bg-(--background-tint-03)" : "bg-transparent"}`}
                    >
                      {row.kind === "folder" ? (
                        <FolderRow folder={row.folder} />
                      ) : (
                        <DocRow hit={row.hit} />
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
});

function FolderRow({ folder }: { folder: FolderHit }) {
  const leaf = folder.path.split("/").pop() || folder.path;
  const parent = folder.path.includes("/")
    ? folder.path.slice(0, folder.path.lastIndexOf("/"))
    : "";
  return (
    <div className="flex items-center gap-2 min-w-0">
      <span className="text-(--text-03) flex shrink-0">
        <SvgFolder size={16} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-[13px] font-semibold text-(--text-05) overflow-hidden text-ellipsis whitespace-nowrap">
          {leaf}
        </div>
        {parent && (
          <div className="text-[11px] text-(--text-03) mt-0.5 font-mono overflow-hidden text-ellipsis whitespace-nowrap">
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
      <div className="text-[13px] font-semibold text-(--text-05) overflow-hidden text-ellipsis whitespace-nowrap">
        {hit.title || hit.path}
      </div>
      <div className="text-[11px] text-(--text-03) mt-0.5 font-mono overflow-hidden text-ellipsis whitespace-nowrap">
        {hit.path}
      </div>
      {hit.snippet && (
        <div className="text-xs text-(--text-04) mt-1 leading-[1.4] line-clamp-2">
          <SnippetText text={hit.snippet} />
        </div>
      )}
    </>
  );
}

// Snippets come back with **match** markers around hit terms. Render those
// as bold spans without going through a full markdown pipeline.
function SnippetText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((p, i) => {
        if (p.startsWith("**") && p.endsWith("**")) {
          return (
            <strong key={i} className="text-(--text-05) font-bold">
              {p.slice(2, -2)}
            </strong>
          );
        }
        return <span key={i}>{p}</span>;
      })}
    </>
  );
}
