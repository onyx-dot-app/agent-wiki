// Wiki URL construction.
//
// Every wiki URL is id-based: `/app/wiki/<wiki_doc_id>`. The id is stable, so
// the URL survives a rename/move. A legacy path URL (`/app/wiki/<path>`) still
// resolves as input — the route looks up its id and redirects to the id URL.
// `wikiPath()` is kept only for fallbacks (an unsaved doc, or a page with no
// id row yet) and for the new-document flow, which is inherently path-based.

import { mutate } from "swr";

import { apiFetch } from "@/lib/api";

/** Revalidate every wiki-related SWR cache after a create / rename / move /
 * delete / restore: the tree listing, the open doc's id→path resolve
 * (`/wiki/id/<id>`), its content (`/wiki/file…`), recents, starred, trash, and
 * the path→id lookups. Matches both string keys under `/wiki` and the path-id
 * array keys. */
export function revalidateWiki(): Promise<unknown> {
  return mutate((key) => {
    const k = Array.isArray(key) ? key[0] : key;
    return (
      typeof k === "string" &&
      (k.startsWith("/wiki") ||
        k === "id-fallback" ||
        k === "wiki-path-id" ||
        k === "wiki-crumb-ids")
    );
  });
}

// A wiki_doc_id is 16 lowercase hex chars (see backend doc_ids._mint_id).
const DOC_ID_RE = /^[0-9a-f]{16}$/;

/** True if a URL segment is a wiki_doc_id (vs a legacy path segment). */
export function isDocId(segment: string): boolean {
  return DOC_ID_RE.test(segment);
}

/** The id-based wiki URL for a doc: `/app/wiki/<id>`. */
export function wikiHref(id: string): string {
  return `/app/wiki/${id}`;
}

/** Clean path-based wiki URL — fallback for docs without an id (unsaved / not
 * yet backfilled) and the new-document flow. */
export function wikiPath(path: string): string {
  const slug = path
    .split("/")
    .filter(Boolean)
    .map(encodeURIComponent)
    .join("/");
  return slug ? `/app/wiki/${slug}` : "/app/wiki";
}

export interface ResolvedDocId {
  id: string;
  path: string;
  kind: "page" | "folder";
  deleted_at: string | null;
}

/** Resolve a doc id to its current binding (path, kind, deleted state). */
export function resolveDocId(id: string): Promise<ResolvedDocId> {
  return apiFetch<ResolvedDocId>(`/wiki/id/${id}`);
}

interface ResolveIdsResponse {
  items: { path: string; id: string | null }[];
}

/** Bulk path→id. Paths without a live id row are omitted from the map. */
export async function resolveIds(
  paths: string[],
): Promise<Record<string, string>> {
  if (paths.length === 0) return {};
  const res = await apiFetch<ResolveIdsResponse>("/wiki/resolve-ids", {
    method: "POST",
    body: JSON.stringify({ paths }),
  });
  const out: Record<string, string> = {};
  for (const it of res.items) if (it.id) out[it.path] = it.id;
  return out;
}

/** Absolute, shareable wiki URL — the id URL (`/app/wiki/<id>`), so a shared
 * link survives a later rename/move. `extraParams` (e.g. `"comment=abc"`, no
 * leading `?`/`&`) is appended as a query string.
 *
 * A path with no live id row is simply absent from the resolve result — that's
 * a legitimate fallback to a plain path URL. A transient lookup failure, by
 * contrast, *rejects*: we must not hand back a fragile path URL as if it were
 * the durable link, so the caller can surface the failure instead of silently
 * copying a link that may break on the next rename. */
export async function shareableWikiUrl(
  path: string,
  extraParams = "",
): Promise<string> {
  const id = (await resolveIds([path]))[path];
  const base = `${window.location.origin}${id ? wikiHref(id) : wikiPath(path)}`;
  const q = extraParams.replace(/^[?&]/, "");
  return q ? `${base}?${q}` : base;
}
