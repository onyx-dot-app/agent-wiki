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
import { color, radius, shadow } from "@/lib/theme";

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
    <div ref={containerRef} style={{ position: "relative", width: "100%" }}>
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
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            minWidth: "100%",
            width: 360,
            maxWidth: "70vw",
            background: color.bg.page,
            border: `1px solid ${color.border.default}`,
            borderRadius: radius.md,
            boxShadow: shadow.popover,
            maxHeight: 420,
            overflowY: "auto",
            zIndex: 40,
          }}
        >
          {error && (
            <div style={{ padding: 12, fontSize: 13, color: color.state.danger.fg }}>{error}</div>
          )}
          {!error && loading && rows.length === 0 && (
            <div style={{ padding: 12, fontSize: 13, color: color.text.muted }}>Searching…</div>
          )}
          {showEmpty && (
            <div style={{ padding: 12, fontSize: 13, color: color.text.muted }}>No matches.</div>
          )}
          {!error && rows.length > 0 && (
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
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
                      style={{
                        width: "100%",
                        textAlign: "left",
                        padding: "10px 12px",
                        border: "none",
                        background: active ? color.accent.subtleBg : "transparent",
                        cursor: "pointer",
                        display: "block",
                        borderBottom: `1px solid ${color.border.subtle}`,
                      }}
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
    <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
      <span style={{ color: color.text.muted, display: "flex", flexShrink: 0 }}>
        <SvgFolder size={16} />
      </span>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: color.text.primary,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {leaf}
        </div>
        {parent && (
          <div
            style={{
              fontSize: 11,
              color: color.text.muted,
              marginTop: 2,
              fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
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
      <div
        style={{
          fontSize: 13,
          fontWeight: 600,
          color: color.text.primary,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {hit.title || hit.path}
      </div>
      <div
        style={{
          fontSize: 11,
          color: color.text.muted,
          marginTop: 2,
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {hit.path}
      </div>
      {hit.snippet && (
        <div
          style={{
            fontSize: 12,
            color: color.text.secondary,
            marginTop: 4,
            lineHeight: 1.4,
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
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
            <strong key={i} style={{ color: color.text.primary, fontWeight: 700 }}>
              {p.slice(2, -2)}
            </strong>
          );
        }
        return <span key={i}>{p}</span>;
      })}
    </>
  );
}
