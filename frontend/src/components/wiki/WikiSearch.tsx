"use client";

import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import { apiFetch, ApiError } from "@/lib/api";

interface SearchHit {
  doc_id: string;
  path: string;
  title: string | null;
  snippet: string;
  score: number;
}

interface SearchResponse {
  query: string;
  hits: SearchHit[];
}

const DEBOUNCE_MS = 150;
const RESULT_LIMIT = 8;

export function WikiSearch() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  // Track the latest in-flight request so a slower response can't overwrite
  // a fresher one's results.
  const requestSeq = useRef(0);

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setHits([]);
      setLoading(false);
      setError(null);
      return;
    }
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    const handle = setTimeout(() => {
      apiFetch<SearchResponse>(
        `/documents/search?q=${encodeURIComponent(trimmed)}&limit=${RESULT_LIMIT}`,
      )
        .then((r) => {
          if (seq !== requestSeq.current) return;
          setHits(r.hits);
          setActiveIdx(0);
        })
        .catch((e) => {
          if (seq !== requestSeq.current) return;
          setHits([]);
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

  const pick = useCallback(
    (hit: SearchHit) => {
      setOpen(false);
      setQuery("");
      router.push(`/wiki/${hit.path}`);
    },
    [router],
  );

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (!open || hits.length === 0) {
      if (e.key === "Escape") setOpen(false);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => (i + 1) % hits.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => (i - 1 + hits.length) % hits.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const hit = hits[activeIdx];
      if (hit) pick(hit);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  const showDropdown = open && query.trim().length > 0;
  const showEmpty = useMemo(
    () => showDropdown && !loading && !error && hits.length === 0,
    [showDropdown, loading, error, hits.length],
  );

  return (
    <div ref={containerRef} style={{ position: "relative", flex: 1, maxWidth: 560 }}>
      <div style={{ position: "relative" }}>
        <span
          aria-hidden
          style={{
            position: "absolute",
            left: 10,
            top: "50%",
            transform: "translateY(-50%)",
            color: "#9ca3af",
            display: "flex",
          }}
        >
          <SearchIcon />
        </span>
        <input
          type="search"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder="Search wiki…"
          aria-label="Search wiki"
          style={{
            width: "100%",
            padding: "8px 32px 8px 32px",
            border: "1px solid #ddd",
            borderRadius: 8,
            fontSize: 14,
            outline: "none",
            background: "white",
            boxSizing: "border-box",
          }}
        />
        {query && (
          <button
            type="button"
            onClick={() => {
              setQuery("");
              setOpen(false);
            }}
            aria-label="Clear search"
            style={{
              position: "absolute",
              right: 6,
              top: "50%",
              transform: "translateY(-50%)",
              background: "transparent",
              border: "none",
              color: "#9ca3af",
              cursor: "pointer",
              padding: 4,
              display: "flex",
            }}
          >
            ×
          </button>
        )}
      </div>

      {showDropdown && (
        <div
          role="listbox"
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            right: 0,
            background: "white",
            border: "1px solid #e5e5e5",
            borderRadius: 8,
            boxShadow: "0 8px 24px rgba(0,0,0,0.08)",
            maxHeight: 420,
            overflowY: "auto",
            zIndex: 40,
          }}
        >
          {error && (
            <div style={{ padding: 12, fontSize: 13, color: "#991b1b" }}>{error}</div>
          )}
          {!error && loading && hits.length === 0 && (
            <div style={{ padding: 12, fontSize: 13, color: "#6b7280" }}>Searching…</div>
          )}
          {showEmpty && (
            <div style={{ padding: 12, fontSize: 13, color: "#6b7280" }}>No matches.</div>
          )}
          {!error && hits.length > 0 && (
            <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {hits.map((h, i) => {
                const active = i === activeIdx;
                return (
                  <li key={h.doc_id}>
                    <button
                      type="button"
                      onMouseEnter={() => setActiveIdx(i)}
                      onMouseDown={(e) => {
                        // mousedown so the input doesn't blur first.
                        e.preventDefault();
                        pick(h);
                      }}
                      style={{
                        width: "100%",
                        textAlign: "left",
                        padding: "10px 12px",
                        border: "none",
                        background: active ? "#eef2ff" : "transparent",
                        cursor: "pointer",
                        display: "block",
                        borderBottom: "1px solid #f3f4f6",
                      }}
                    >
                      <div
                        style={{
                          fontSize: 13,
                          fontWeight: 600,
                          color: active ? "#3730a3" : "#111",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {h.title || h.path}
                      </div>
                      <div
                        style={{
                          fontSize: 11,
                          color: active ? "#4338ca" : "#6b7280",
                          marginTop: 2,
                          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {h.path}
                      </div>
                      {h.snippet && (
                        <div
                          style={{
                            fontSize: 12,
                            color: "#4b5563",
                            marginTop: 4,
                            lineHeight: 1.4,
                            display: "-webkit-box",
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: "vertical",
                            overflow: "hidden",
                          }}
                        >
                          <SnippetText text={h.snippet} />
                        </div>
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
            <strong key={i} style={{ color: "#111", fontWeight: 700 }}>
              {p.slice(2, -2)}
            </strong>
          );
        }
        return <span key={i}>{p}</span>;
      })}
    </>
  );
}

function SearchIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="11" cy="11" r="7" />
      <path d="m21 21-4.3-4.3" />
    </svg>
  );
}
