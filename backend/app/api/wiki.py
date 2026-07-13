"""FastAPI router for wiki page operations (/api/wiki/*)."""

from __future__ import annotations

import logging
import re
import subprocess
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import User, require_can
from app.auth.deps import require_user
from app.models.file_system import (
    ActivityRowView,
    CommitView,
    CreateFolderRequest,
    CreateFolderResponse,
    DeleteDocumentResponse,
    DocumentActivityResponse,
    DocumentDraftView,
    DocumentEntry,
    FileDiffResponse,
    FileHistoryResponse,
    FolderHitView,
    GenerateDraftRequest,
    GenerateDraftResponse,
    GetDocumentResponse,
    ListDocumentsResponse,
    ListRecentPagesResponse,
    RecentPageView,
    MovedFile,
    MovePathRequest,
    MovePathResponse,
    PutDocumentRequest,
    PutDocumentResponse,
    RecentDocsResponse,
    RecordRecentDocRequest,
    ReindexRequest,
    ReindexResponse,
    ReorderStarredRequest,
    ReviseDraftRequest,
    ReviseDraftResponse,
    SearchHitView,
    SearchResponse,
    SetDocumentDraftRequest,
    StarDocRequest,
    StarredDocsResponse,
)
from app.tasks.reindex import index_path
from app.triggers import repo as triggers_repo
from app.wiki import (
    acl,
    agent_activity,
    coedit,
    diff as wiki_diff,
    drafts as wiki_drafts,
    filesystem,
    git as wiki_git,
    notify as wiki_notify,
    recents as wiki_recents,
    search as wiki_search,
    starred as wiki_starred,
    templates as templates_repo,
    update_policy as update_policy_repo,
    utils as wiki_utils,
)
from app.ingest import settings as ingest_settings
from app.models.update_policy import UpdateHealthResponse
from app.models.wiki import ChangeKind, CommitMaxRetriesError, PathMove

router = APIRouter()
log = logging.getLogger(__name__)

# A rollback-edit records the SHAs it supersedes in a "Deprecates:" trailer
# in the new commit body. The history endpoint hides any sha listed in any
# later commit's trailer, so rolled-back-over revisions disappear without
# rewriting git history.
_DEPRECATES_RE = re.compile(r"^Deprecates:\s*(.+)$", re.MULTILINE)
_SHA_RE = re.compile(r"^[0-9a-f]{4,40}$")

_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
# Preview payload cap per card — enough to fill the masked card, bounded so
# a wide "recent" fan-out doesn't ship whole documents to the client.
_PREVIEW_MAX_CHARS = 600


def _title_and_preview(body: str, path: str) -> tuple[str, str]:
    """Split a doc body into a card title + masked preview.

    Title = leading ``# H1`` if the body opens with one (after any YAML
    frontmatter), else the filename without ``.md``. Preview = the body
    with frontmatter and that leading heading removed, capped.
    """
    stripped = _FRONTMATTER_RE.sub("", body, count=1).lstrip("\n")
    title = ""
    newline = stripped.find("\n")
    first_line = stripped if newline == -1 else stripped[:newline]
    if first_line.startswith("# "):
        title = first_line[2:].strip()
        stripped = "" if newline == -1 else stripped[newline + 1 :].lstrip("\n")
    if not title:
        base = path.rsplit("/", 1)[-1]
        title = base[:-3] if base.endswith(".md") else base
    return title, stripped[:_PREVIEW_MAX_CHARS]


def _git_author(user: User | None) -> str | None:
    if user is None:
        return None
    return f"{user.name or user.email} <{user.email}>"


def _seed_template_policy(
    rel: str, actor_user_id: str, template_id: str | None = None
) -> None:
    """Seed a new page's update policy from the template it was created from.

    If that template carries a default policy (auto-update off, scope/update
    instruction), apply it to the page's ``update_policies`` row. Only fields
    the template actually sets are written — the rest stay inherited.

    ``template_id`` comes from the create request (the reliable source — the
    new-doc UI records its draft *after* the create commits). Falls back to the
    page's new-doc draft when not supplied.
    """
    tid = template_id
    if tid is None:
        draft = wiki_drafts.get(rel)
        tid = draft.get("template_id") if draft else None
    if not tid:
        return
    templates_repo.apply_policy_to_page(rel, tid, actor_user_id)


@router.get("", response_model=ListDocumentsResponse)
def list_documents(
    user: User = Depends(require_user),
    prefix: str = "",
) -> ListDocumentsResponse:
    raw = wiki_git.list_paths_with_mtime(prefix)
    if not user.is_admin:
        from app.wiki import acl as _acl

        md_paths = [p for p, _ in raw if p.endswith(".md")]
        visible = set(_acl.filter_paths_in_python(user.id, False, md_paths))
        # Keep non-md paths (folders, .gitkeep) so the explorer can render
        # the tree; permission checks happen on actual page access.
        raw = [(p, ts) for p, ts in raw if not p.endswith(".md") or p in visible]
    entries = [DocumentEntry(path=p, updated_at=ts) for p, ts in raw]
    return ListDocumentsResponse(entries=entries)


@router.get("/recent", response_model=ListRecentPagesResponse)
def list_recent_pages(
    user: User = Depends(require_user),
    limit: int = Query(12, ge=1, le=50),
) -> ListRecentPagesResponse:
    """Pages the current user has worked on, with a title + masked preview.

    Feeds the home-page "Recent Pages" grid. Sourced from the user's own
    git authorship (pages they created or edited), newest-first; still
    ACL-filtered so a page they can no longer see is dropped.
    """
    raw = wiki_git.paths_authored_by(user.email)
    md = [(p, ts) for p, ts in raw if p.endswith(".md")]
    if not user.is_admin:
        from app.wiki import acl as _acl

        visible = set(_acl.filter_paths_in_python(user.id, False, [p for p, _ in md]))
        md = [(p, ts) for p, ts in md if p in visible]
    # Newest first; empty timestamps sink to the bottom.
    md.sort(key=lambda pt: pt[1], reverse=True)
    pages: list[RecentPageView] = []
    for path, ts in md[:limit]:
        abs_path = filesystem.absolute(path)
        if not abs_path.is_file():
            continue
        try:
            body = abs_path.read_text()
        except OSError:
            continue
        title, preview = _title_and_preview(body, path)
        pages.append(RecentPageView(path=path, title=title, updated_at=ts, preview=preview))
    return ListRecentPagesResponse(pages=pages)


@router.get("/file", response_model=GetDocumentResponse)
def get_document_by_path(
    user: User = Depends(require_user),
    path: str = "",
    ref: str | None = None,
) -> GetDocumentResponse:
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    require_can("read", rel, user)
    head_sha = wiki_git.head_sha_for_path(rel)
    if ref:
        # The path may have been different at this ref (rename). Resolve
        # via --follow so old commits don't 404 on the current name.
        historical = wiki_git.path_at_ref(rel, ref) or rel
        try:
            body = wiki_git.read_file(historical, ref=ref)
        except wiki_git.UnknownSha as exc:
            raise HTTPException(status_code=404, detail="not found at ref") from exc
        return GetDocumentResponse(path=rel, body=body, head_sha=head_sha, ref=ref)
    # Session-aware live read: when a co-edit session is open on this page, its
    # Postgres buffer holds the freshest edits. The checkpoint that commits the
    # buffer to git runs asynchronously, so HEAD lags — reading it would show
    # stale content right after a save. Serve a quick, display-only 3-way merge
    # of HEAD + the live buffer so a viewer sees both committed edits and
    # in-session edits without waiting on the commit. Best-effort and
    # non-authoritative (no LLM, nothing persisted): on a merge conflict, prefer
    # the live buffer. This is a UI read; git stays the source of truth for
    # committed pages.
    sess = coedit.get_active_session(rel)
    if sess is not None:
        body = sess.buffer_text
        # Fast path: if HEAD hasn't moved since the session opened (the common
        # case — live-rebase folds inbound agent commits into the buffer and
        # advances base_sha), the buffer already reflects everything, so skip
        # the merge subprocess. When HEAD has advanced past the session's base,
        # reconcile the committed change with the buffer for display: a clean
        # 3-way merge shows both; on a conflict — or a merge failure — serve
        # committed HEAD, not the buffer. Preferring the buffer there would let
        # a stale/lagging session (in the limit, a zombie with no participants
        # left to reconcile it) hide the committed change from every viewer —
        # the 2026-07-06 incident. The authoritative merge happens at checkpoint.
        if sess.base_sha is not None and sess.base_sha != head_sha:
            base = wiki_git.read_file_opt(rel, ref=sess.base_sha) or ""
            current = wiki_git.read_file_opt(rel) or ""
            try:
                merge = wiki_git.merge_content(base, current, sess.buffer_text)
                body = merge.merged if merge.clean else current
            except RuntimeError:
                log.warning(
                    "coedit read: merge_content failed for %s (base_sha=%s); serving HEAD",
                    rel,
                    sess.base_sha,
                    exc_info=True,
                )
                body = current
        return GetDocumentResponse(path=rel, body=body, head_sha=head_sha)
    abs_path = filesystem.absolute(rel)
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return GetDocumentResponse(path=rel, body=abs_path.read_text(), head_sha=head_sha)


@router.put("/file", response_model=PutDocumentResponse)
def put_document_by_path(
    req: PutDocumentRequest,
    user: User = Depends(require_user),
) -> PutDocumentResponse:
    try:
        rel = filesystem.safe_rel_path(req.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not rel.endswith(".md"):
        raise HTTPException(status_code=400, detail="only .md files are supported")
    abs_path = filesystem.absolute(rel)
    existed = abs_path.is_file()
    if existed:
        # Editing an existing page requires write on *that* page. Creating
        # a new page is always allowed for an authenticated user; the
        # creator becomes the owner and gets full rights.
        require_can("write", rel, user)
    # Validate an explicit create-from-template id up front — before any
    # commit — so a stale/deleted template_id fails the request instead of
    # silently creating a page with no policy applied.
    if not existed and req.template_id is not None:
        if templates_repo.get(req.template_id) is None:
            raise HTTPException(status_code=404, detail="template not found")
    author = _git_author(user)
    change_kind = ChangeKind.EDIT if existed else ChangeKind.CREATE
    msg = f"{change_kind} {rel}"
    # Read-modify-write: only when editing an existing page from a known base.
    # New pages (and force-saves with no base_sha) commit as-is.
    base_body = wiki_git.read_file(rel, ref=req.base_sha) if existed and req.base_sha else None
    try:
        # skip_acl: the write gate already ran above via require_can. ai_merge
        # is off so an unresolvable merge raises -> 409 (the conflict UI).
        result = wiki_utils.commit_and_fan_out(
            path=rel,
            body=req.body,
            message=msg,
            change_kind=change_kind,
            base_body=base_body,
            skip_acl=True,
            record_activity=False,
        )
    except (wiki_git.GitMergeConflictError, CommitMaxRetriesError):
        raise HTTPException(status_code=409, detail="conflict detected")
    if result is None:
        # Merge produced no change — the submitted body already matches HEAD.
        sha = wiki_git.head_sha_for_path(rel) or ""
        body_to_commit = req.body
    else:
        sha = result.sha
        body_to_commit = result.new_body
    # On first create, seed the page's update policy from its template (before
    # the draft row may be cleared below).
    if not existed:
        _seed_template_policy(rel, user.id, req.template_id)
    # Drafting state: if the saved body diverges from the template
    # snapshot, the user has made it their own — clear the row so the
    # chat banner drops and the template's system prompt stops applying.
    wiki_drafts.clear_if_diverged(rel, body_to_commit)
    log.info("doc %s %s by %s sha=%s", change_kind, rel, author or "?", sha[:8])
    return PutDocumentResponse(
        path=rel,
        sha=sha,
        created=not existed,
        deprecated=[],
    )


@router.post(
    "/folder",
    response_model=CreateFolderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_folder(
    req: CreateFolderRequest,
    user: User = Depends(require_user),
) -> CreateFolderResponse:
    """Create an (empty) wiki folder via a `.gitkeep` marker."""
    path = req.path.strip().strip("/")
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if rel.endswith(".md"):
        raise HTTPException(status_code=400, detail="folder path must not end in .md")
    abs_path = filesystem.absolute(rel)
    if abs_path.is_file():
        raise HTTPException(status_code=409, detail="a file with that path already exists")
    if abs_path.is_dir():
        raise HTTPException(status_code=409, detail="folder already exists")
    author = _git_author(user)
    sha = wiki_git.commit_file(f"{rel}/.gitkeep", "", f"create folder {rel}", author=author)
    log.info("folder created %s by %s sha=%s", rel, author or "?", sha[:8])
    return CreateFolderResponse(path=rel, sha=sha)


@router.post("/move", response_model=MovePathResponse)
def move_document_or_folder(
    req: MovePathRequest,
    user: User = Depends(require_user),
) -> MovePathResponse:
    """Rename or relocate a document or folder, single git commit."""
    old_raw = req.old_path.strip().strip("/")
    new_raw = req.new_path.strip().strip("/")
    if not old_raw or not new_raw:
        raise HTTPException(status_code=400, detail="old_path and new_path required")
    try:
        old_rel = filesystem.safe_rel_path(old_raw)
        new_rel = filesystem.safe_rel_path(new_raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if old_rel == new_rel:
        raise HTTPException(status_code=400, detail="old_path and new_path are identical")
    old_abs = filesystem.absolute(old_rel)
    new_abs = filesystem.absolute(new_rel)
    if not old_abs.exists():
        raise HTTPException(status_code=404, detail="not found")
    if new_abs.exists():
        raise HTTPException(
            status_code=409,
            detail="a file or folder with that name already exists",
        )
    # An active co-edit session can hold a not-yet-committed draft at the
    # destination — no file on disk, so the exists() check above misses it.
    blocking = coedit.blocking_active_session_path(new_rel)
    if blocking is not None:
        raise HTTPException(
            status_code=409,
            detail=f"someone is editing an unsaved draft at '{blocking}' — "
            "pick a different name or wait for it to be saved",
        )
    if old_abs.is_file() and old_rel.endswith(".md") and not new_rel.endswith(".md"):
        raise HTTPException(
            status_code=400,
            detail="renaming a .md file requires the new name to end in .md",
        )
    if old_abs.is_dir() and new_rel.endswith(".md"):
        raise HTTPException(status_code=400, detail="folder name must not end in .md")

    # Moving a page requires write on it. Moving a folder requires write
    # on every page underneath it (we expand the check at write time so
    # the whole move is atomic — refuse if any page is denied).
    if old_abs.is_file() and old_rel.endswith(".md"):
        require_can("write", old_rel, user)
    elif old_abs.is_dir():
        for p in wiki_git.list_paths(old_rel):
            if p.endswith(".md"):
                require_can("write", p, user)

    author = _git_author(user)
    msg = f"move {old_rel} -> {new_rel}"
    try:
        sha, moves = wiki_git.move_path(old_rel, new_rel, msg, author=author)
    except subprocess.CalledProcessError as exc:
        log.warning("move_path git error %s -> %s: %s", old_rel, new_rel, exc.stderr)
        raise HTTPException(status_code=500, detail="git move failed") from exc

    # after_path_move re-points every live path-keyed cache (ACL, comments,
    # activity, drafts, working-dirs) and reconverges the trigger cache.
    # root_move is the actual rename so folder-level grants re-point correctly
    # even when all of a folder's files sit in one subdirectory.
    wiki_notify.after_path_move(
        moves, sha, author, root_move=PathMove(old=old_rel, new=new_rel)
    )

    log.info(
        "move %s -> %s by %s sha=%s files=%d", old_rel, new_rel, author or "?", sha[:8], len(moves)
    )
    return MovePathResponse(
        old_path=old_rel,
        new_path=new_rel,
        sha=sha,
        moved=[MovedFile(old=mv.old, new=mv.new) for mv in moves],
    )


@router.delete("/file", response_model=DeleteDocumentResponse)
def delete_document_by_path(
    user: User = Depends(require_user),
    path: str = "",
) -> DeleteDocumentResponse:
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    abs_path = filesystem.absolute(rel)
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="not found")
    # The .md pages being removed: the file itself, or every page under a
    # folder. We snapshot these *before* the delete so the post-delete fan-out
    # (which drops each page's caches) covers a folder delete too.
    md_paths: list[str]
    if abs_path.is_file() and rel.endswith(".md"):
        md_paths = [rel]
    elif abs_path.is_dir():
        md_paths = [p for p in wiki_git.list_paths(rel) if p.endswith(".md")]
    else:
        md_paths = []
    for p in md_paths:
        require_can("write", p, user)
    author = _git_author(user)
    sha = wiki_git.delete_path(rel, f"delete {rel}", author=author)
    for p in md_paths:
        # Drops FTS / ACL / comments / activity / working-dir state for each
        # removed page.
        wiki_notify.after_doc_delete(p, sha, author)
    log.info("doc deleted %s (%d pages) by %s sha=%s", rel, len(md_paths), author or "?", sha[:8])
    return DeleteDocumentResponse(sha=sha)


@router.post("/reindex", response_model=ReindexResponse)
def reindex_document_by_path(
    req: ReindexRequest,
    user: User = Depends(require_user),
) -> ReindexResponse:
    try:
        rel = filesystem.safe_rel_path(req.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    abs_path = filesystem.absolute(rel)
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    require_can("read", rel, user)
    index_path(rel)
    return ReindexResponse(path=rel, queued=True)


@router.post("/generate", response_model=GenerateDraftResponse)
def generate_draft(
    req: GenerateDraftRequest,
    user: User = Depends(require_user),
) -> GenerateDraftResponse:
    """Generate a draft (title + body) from a free-text prompt for review."""
    from app.llm.agents import draft_generator

    result = draft_generator.generate(req.prompt)
    return GenerateDraftResponse(title=result["title"], body=result["body"])


@router.post("/revise", response_model=ReviseDraftResponse)
def revise_draft(
    req: ReviseDraftRequest,
    user: User = Depends(require_user),
) -> ReviseDraftResponse:
    """Apply an instruction to an unsaved draft body; return the revised body."""
    from app.llm.agents import draft_reviser

    return ReviseDraftResponse(body=draft_reviser.revise(req.body, req.instruction))


@router.get("/search", response_model=SearchResponse)
def search_documents(
    user: User = Depends(require_user),
    q: str = "",
    limit: int = Query(10, ge=1, le=50),
) -> SearchResponse:
    query = q.strip()
    if not query:
        return SearchResponse(query="", hits=[])
    folders = wiki_search.search_folders(query, limit=limit)
    # Folders take precedence in the dropdown; docs share the remaining
    # budget so the combined list never exceeds ``limit``.
    doc_limit = max(1, limit - len(folders))
    hits = wiki_search.search(
        query,
        limit=doc_limit,
        user_id=user.id,
        is_admin=user.is_admin,
    )
    return SearchResponse(
        query=query,
        hits=[
            SearchHitView(
                doc_id=h.doc_id,
                path=h.path,
                title=h.title,
                snippet=h.snippet,
                score=h.score,
            )
            for h in hits
        ],
        folders=[FolderHitView(path=f.path) for f in folders],
    )


@router.get("/recents", response_model=RecentDocsResponse)
def list_recent_docs(user: User = Depends(require_user)) -> RecentDocsResponse:
    paths = wiki_recents.list_paths(user.id)
    # Drop docs deleted since they were viewed; ACL changes can also
    # revoke access after the fact, so re-filter on every read.
    paths = [p for p in paths if filesystem.absolute(p).is_file()]
    if not user.is_admin:
        from app.wiki import acl as _acl

        paths = _acl.filter_paths_in_python(user.id, False, paths)
    return RecentDocsResponse(paths=paths)


@router.post("/recents", status_code=status.HTTP_204_NO_CONTENT)
def record_recent_doc(
    req: RecordRecentDocRequest,
    user: User = Depends(require_user),
) -> None:
    try:
        rel = filesystem.safe_rel_path(req.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    require_can("read", rel, user)
    wiki_recents.record_view(user.id, rel)


@router.get("/starred", response_model=StarredDocsResponse)
def list_starred_docs(user: User = Depends(require_user)) -> StarredDocsResponse:
    paths = wiki_starred.list_paths(user.id)
    # Same read-side filtering as recents: deletions and ACL changes
    # after the star must hide the entry.
    paths = [p for p in paths if filesystem.absolute(p).is_file()]
    if not user.is_admin:
        from app.wiki import acl as _acl

        paths = _acl.filter_paths_in_python(user.id, False, paths)
    return StarredDocsResponse(paths=paths)


@router.post("/starred", status_code=status.HTTP_204_NO_CONTENT)
def star_doc(
    req: StarDocRequest,
    user: User = Depends(require_user),
) -> None:
    try:
        rel = filesystem.safe_rel_path(req.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    require_can("read", rel, user)
    wiki_starred.star(user.id, rel)


@router.delete("/starred", status_code=status.HTTP_204_NO_CONTENT)
def unstar_doc(
    user: User = Depends(require_user),
    path: str = "",
) -> None:
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # No read gate — users can always remove their own pin, even when
    # access was revoked after they starred it.
    wiki_starred.unstar(user.id, rel)


@router.put("/starred", status_code=status.HTTP_204_NO_CONTENT)
def reorder_starred_docs(
    req: ReorderStarredRequest,
    user: User = Depends(require_user),
) -> None:
    try:
        rels = [filesystem.safe_rel_path(p) for p in req.paths]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    wiki_starred.reorder(user.id, rels)


@router.get("/file/history", response_model=FileHistoryResponse)
def file_history(
    user: User = Depends(require_user),
    path: str = "",
) -> FileHistoryResponse:
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    require_can("read", rel, user)
    rows = wiki_git.history(rel)
    deprecated: set[str] = set()
    for r in rows:
        for m in _DEPRECATES_RE.finditer(r.body or ""):
            for token in m.group(1).split():
                deprecated.add(token)
    head_sha = rows[0].sha if rows else None
    fires = triggers_repo.fire_counts_by_sha({r.sha for r in rows})
    visible = [
        CommitView(
            sha=r.sha,
            author=r.author,
            ts=r.ts,
            message=r.message,
            body=r.body,
            added=r.added,
            removed=r.removed,
            triggered=fires.get(r.sha, 0),
        )
        for r in rows
        if r.sha not in deprecated
    ]
    return FileHistoryResponse(path=rel, head_sha=head_sha, commits=visible)


@router.get("/update-health", response_model=UpdateHealthResponse)
def update_health(
    user: User = Depends(require_user),
    path: str = "",
) -> UpdateHealthResponse:
    """Auto-update health facts for a page: the 24h ingestion-update count, the
    page's resolved warning threshold, and the admin cap. The client decides
    what to surface (threshold slider value/max, too-frequent-update banner)."""
    try:
        rel = update_policy_repo.normalize_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    require_can("read", rel, user)

    times = wiki_git.ingest_update_times_24h(rel)
    count = len(times)
    cap = ingest_settings.get().auto_update_cap
    # When over the cap, the page resumes once enough of the oldest in-window
    # updates age out for the count to drop back under the cap: the
    # (count - cap + 1)-th oldest update (index count - cap) leaves the window
    # 24h after it landed.
    cap_resets_at: str | None = None
    if cap > 0 and count >= cap:
        reset = datetime.fromtimestamp(times[count - cap], tz=timezone.utc) + timedelta(
            hours=24
        )
        cap_resets_at = reset.isoformat()
    return UpdateHealthResponse(
        path=rel,
        count_24h=count,
        threshold_24h=update_policy_repo.resolve_warn_threshold(rel),
        cap_24h=cap,
        auto_update_disabled=update_policy_repo.resolve_for_path(
            rel
        ).ingestion_auto_update_disabled,
        can_manage=acl.can(user.id, user.is_admin, "write", rel),
        cap_resets_at=cap_resets_at,
    )


@router.get("/file/diff", response_model=FileDiffResponse)
def file_diff(
    user: User = Depends(require_user),
    path: str = "",
    sha: str = "",
) -> FileDiffResponse:
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    if not sha:
        raise HTTPException(status_code=400, detail="sha required")
    if not _SHA_RE.match(sha):
        raise HTTPException(status_code=400, detail="malformed sha")
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    require_can("read", rel, user)
    result = wiki_diff.parse_commit_diff(sha, rel)
    if not result.hunks:
        raise HTTPException(status_code=404, detail="sha does not touch path")
    return result


@router.get("/file/activity", response_model=DocumentActivityResponse)
def file_activity(
    user: User = Depends(require_user),
    path: str = "",
) -> DocumentActivityResponse:
    """Active agent-activity rows for a doc."""
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    require_can("read", rel, user)
    rows = agent_activity.list_for_doc(rel)
    return DocumentActivityResponse(
        path=rel,
        agents=[
            ActivityRowView(
                owner_display=r.owner_display,
                agent_name=r.agent_name,
                activity=r.activity,
                description=r.description,
                registered_at=r.registered_at,
                expires_at=r.expires_at,
                agent_session_id=r.agent_session_id,
            )
            for r in rows
        ],
    )


@router.get("/file/draft", response_model=DocumentDraftView | None)
def file_draft(
    user: User = Depends(require_user),
    path: str = "",
) -> DocumentDraftView | None:
    """Return active "drafting from template" state for a doc, or null.

    Reconciles divergence at read time as well as write time: if the
    current body no longer matches the template snapshot — e.g. an
    agent edited the page through a path that didn't route through the
    user PUT handler — the draft row is cleared here and the response
    is ``null``. That way revisiting the doc shows a normal chat, not
    the drafting banner.
    """
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    require_can("read", rel, user)
    row = wiki_drafts.get(rel)
    if row is None:
        return None
    abs_path = filesystem.absolute(rel)
    current_body = abs_path.read_text() if abs_path.is_file() else ""
    if wiki_drafts.clear_if_diverged(rel, current_body):
        return None
    return DocumentDraftView(
        path=row["path"],
        template_id=row["template_id"],
        template_name=row["template_name"],
        system_prompt=row["system_prompt"],
        created_at=row["created_at"],
    )


@router.post("/file/draft", response_model=DocumentDraftView | None)
def set_file_draft(
    req: SetDocumentDraftRequest,
    user: User = Depends(require_user),
) -> DocumentDraftView | None:
    """Record that ``path`` is being drafted from ``template_id`` — or
    clear the record when ``template_id`` is null.

    Called by the wiki editor's inline template gallery: clicking a
    template applies its body to the local editor state and posts here
    so the chat widget can drop into drafting mode for the live session.
    """
    try:
        rel = filesystem.safe_rel_path(req.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    require_can("write", rel, user)
    if req.template_id is None:
        wiki_drafts.delete(rel)
        return None
    tmpl = templates_repo.get(req.template_id)
    if tmpl is None:
        raise HTTPException(status_code=404, detail="template not found")
    wiki_drafts.create(
        path=rel,
        template_id=tmpl["id"],
        template_body_snapshot=tmpl["body"],
        created_by_user_id=user.id,
    )
    row = wiki_drafts.get(rel)
    assert row is not None
    return DocumentDraftView(
        path=row["path"],
        template_id=row["template_id"],
        template_name=row["template_name"],
        system_prompt=row["system_prompt"],
        created_at=row["created_at"],
    )


@router.get("/{doc_id}")
def get_document(doc_id: str, _user: User = Depends(require_user)) -> None:
    # TODO: read from git working tree, return body + metadata
    raise NotImplementedError


@router.put("/{doc_id}")
def update_document(doc_id: str, _user: User = Depends(require_user)) -> None:
    # Used by agents directly editing a doc.
    raise NotImplementedError


@router.get("/{doc_id}/history")
def document_history(doc_id: str, _user: User = Depends(require_user)) -> None:
    raise NotImplementedError
