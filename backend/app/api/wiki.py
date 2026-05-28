"""FastAPI router for wiki page operations (/api/wiki/*)."""

from __future__ import annotations

import logging
import re
import subprocess

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import User, require_can
from app.auth.deps import require_user
from app.llm.agents import merge_conflict_update
from app.models.file_system import (
    ActivityRowView,
    CommitView,
    CreateFolderRequest,
    CreateFolderResponse,
    DeleteDocumentResponse,
    DocumentActivityResponse,
    DocumentDraftView,
    DocumentEntry,
    DraftRequest,
    DraftResponse,
    FileHistoryResponse,
    FolderHitView,
    GetDocumentResponse,
    ListDocumentsResponse,
    MergeRequest,
    MergeResponse,
    MovedFile,
    MovePathRequest,
    MovePathResponse,
    PutDocumentRequest,
    PutDocumentResponse,
    RebaseConflictResponse,
    RebaseRequest,
    ReindexRequest,
    ReindexResponse,
    SearchHitView,
    SearchResponse,
    SetDocumentDraftRequest,
)
from app.tasks.reindex import index_path
from app.triggers import repo as triggers_repo
from app.wiki import (
    agent_activity,
    drafts as wiki_drafts,
    filesystem,
    git as wiki_git,
    notify as wiki_notify,
    search as wiki_search,
    templates as templates_repo,
)
from app.wiki.models import ChangeKind

router = APIRouter()
log = logging.getLogger(__name__)

# A rollback-edit records the SHAs it supersedes in a "Deprecates:" trailer
# in the new commit body. The history endpoint hides any sha listed in any
# later commit's trailer, so rolled-back-over revisions disappear without
# rewriting git history.
_DEPRECATES_RE = re.compile(r"^Deprecates:\s*(.+)$", re.MULTILINE)


def _git_author(user: User | None) -> str | None:
    if user is None:
        return None
    return f"{user.name or user.email} <{user.email}>"


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
        except subprocess.CalledProcessError as exc:
            raise HTTPException(status_code=404, detail="not found at ref") from exc
        return GetDocumentResponse(path=rel, body=body, head_sha=head_sha, ref=ref)
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
    author = _git_author(user)
    change_kind = ChangeKind.EDIT if existed else ChangeKind.CREATE
    msg = f"{change_kind} {rel}"
    body_to_commit = req.body
    if req.base_sha:
        head = wiki_git.head_sha_for_path(rel)
        if head and head != req.base_sha:
            # The page changed since the client opened it. Attempt a 3-way
            # merge: if the edits don't overlap, commit the merged result
            # transparently. Only return 409 when there are actual conflicts.
            try:
                base_body = wiki_git.read_file(rel, ref=req.base_sha)
                current_body = abs_path.read_text()
                mr = wiki_git.merge_content(base_body, current_body, req.body)
            except (subprocess.CalledProcessError, RuntimeError):
                raise HTTPException(status_code=409, detail="conflict detected")
            if not mr.clean:
                raise HTTPException(status_code=409, detail="conflict detected")
            body_to_commit = mr.merged
            log.info("doc auto-merged %s by %s", rel, author or "?")
    sha = wiki_git.commit_file(rel, body_to_commit, msg, author=author)
    wiki_notify.after_doc_write(
        rel,
        sha,
        change_kind,
        author,
        owner_user_id=user.id if change_kind == ChangeKind.CREATE else None,
    )
    # Drafting state: if the saved body diverges from the template
    # snapshot, the user has made it their own — clear the row so the
    # chat banner drops and the template's system prompt stops applying.
    wiki_drafts.clear_if_diverged(rel, body_to_commit)
    # Edit draft is no longer needed after a successful commit.
    wiki_git.delete_draft(rel, user.id)
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

    wiki_notify.after_path_move(moves, sha, author)

    # Trigger YAML files may have moved with their containing folder. The
    # Postgres cache stores their absolute file_path; reconverge from disk.
    try:
        triggers_repo.rebuild_from_filesystem()
    except Exception:
        log.exception("trigger cache rebuild after move %s -> %s failed", old_rel, new_rel)

    log.info(
        "move %s -> %s by %s sha=%s files=%d", old_rel, new_rel, author or "?", sha[:8], len(moves)
    )
    return MovePathResponse(
        old_path=old_rel,
        new_path=new_rel,
        sha=sha,
        moved=[MovedFile(old=o, new=n) for o, n in moves],
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
    if abs_path.is_file() and rel.endswith(".md"):
        require_can("write", rel, user)
    elif abs_path.is_dir():
        for p in wiki_git.list_paths(rel):
            if p.endswith(".md"):
                require_can("write", p, user)
    author = _git_author(user)
    sha = wiki_git.delete_path(rel, f"delete {rel}", author=author)
    wiki_notify.after_doc_delete(rel, sha, author)
    wiki_drafts.delete(rel)
    wiki_git.delete_drafts_for_path(rel)
    log.info("doc deleted %s by %s sha=%s", rel, author or "?", sha[:8])
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
    visible = [
        CommitView(sha=r.sha, author=r.author, ts=r.ts, message=r.message, body=r.body)
        for r in rows
        if r.sha not in deprecated
    ]
    return FileHistoryResponse(path=rel, head_sha=head_sha, commits=visible)


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


@router.get("/file/autosave", response_model=DraftResponse | None)
def get_draft(
    user: User = Depends(require_user),
    path: str = "",
) -> DraftResponse | None:
    """Return the user's in-progress draft for a page, or null."""
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    require_can("read", rel, user)
    row = wiki_git.get_draft(rel, user.id)
    if row is None:
        return None
    return DraftResponse(
        path=row["path"],
        base_sha=row["base_sha"],
        content=row["content"],
        updated_at=row["updated_at"],
    )


@router.put("/file/autosave", response_model=DraftResponse)
def upsert_draft(
    req: DraftRequest,
    user: User = Depends(require_user),
) -> DraftResponse:
    """Auto-save the user's in-progress draft. Returns the saved draft."""
    try:
        rel = filesystem.safe_rel_path(req.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not filesystem.absolute(rel).is_file():
        raise HTTPException(status_code=404, detail="not found")
    require_can("write", rel, user)
    wiki_git.save_draft(rel, user.id, req.content, req.base_sha)
    row = wiki_git.get_draft(rel, user.id)
    assert row is not None
    return DraftResponse(
        path=row["path"],
        base_sha=row["base_sha"],
        content=row["content"],
        updated_at=row["updated_at"],
    )


@router.delete("/file/autosave", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(
    user: User = Depends(require_user),
    path: str = "",
) -> None:
    """Clear the user's in-progress draft for a page."""
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    wiki_git.delete_draft(rel, user.id)


@router.post("/file/autosave/rebase", response_model=DraftResponse)
def rebase_draft(
    req: RebaseRequest,
    user: User = Depends(require_user),
) -> DraftResponse:
    """3-way merge the user's draft onto the current HEAD.

    Returns the merged draft (200) when git merge-file produces no conflict
    markers, saving the rebased draft automatically.  Returns 409 with
    ``RebaseConflictResponse`` when conflicts need human resolution.
    Returns 404 when the page or draft does not exist.
    """
    try:
        rel = filesystem.safe_rel_path(req.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not filesystem.absolute(rel).is_file():
        raise HTTPException(status_code=404, detail="not found")
    require_can("write", rel, user)

    result = wiki_git.rebase_draft(rel, user.id)
    if result is None:
        raise HTTPException(status_code=404, detail="no draft found")

    if result.clean:
        wiki_git.save_draft(rel, user.id, result.merged, result.base_sha)
        row = wiki_git.get_draft(rel, user.id)
        if row is None:
            raise HTTPException(status_code=500, detail="draft vanished after save")
        return DraftResponse(
            path=row["path"],
            base_sha=row["base_sha"],
            content=row["content"],
            updated_at=row["updated_at"],
        )

    raise HTTPException(
        status_code=409,
        detail=RebaseConflictResponse(
            current_body=result.current_body,
            draft_body=result.draft_body,
            current_sha=result.base_sha,
        ).model_dump(),
    )


@router.post("/file/merge", response_model=MergeResponse)
def merge_draft(
    req: MergeRequest,
    user: User = Depends(require_user),
) -> MergeResponse:
    """LLM 3-way merge: combine current HEAD and the user's draft.

    Returns the merged body for the user to review before saving.
    """
    try:
        rel = filesystem.safe_rel_path(req.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    require_can("write", rel, user)
    try:
        base_body = wiki_git.read_file(rel, ref=req.base_sha)
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=404, detail="base revision not found") from exc
    # Fetch the most recent commit message so the LLM can reference it when
    # annotating conflicting facts (e.g. "12k from fix: update connection limit").
    current_commits = wiki_git.history(rel, limit=1)
    current_commit_message = current_commits[0].message if current_commits else None
    try:
        merged = merge_conflict_update.merge(
            wiki_path=rel,
            base_body=base_body,
            current_body=req.current_body,
            draft_body=req.draft_body,
            current_commit_message=current_commit_message,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return MergeResponse(merged=merged)


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
