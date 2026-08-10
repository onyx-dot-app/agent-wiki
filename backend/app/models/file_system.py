"""HTTP shapes for /api/wiki."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.wiki import Attribution, PageKind, SourceRef


# --------------------------------------------------------------------------- #
# Existing schemas (kept for forward compatibility)                           #
# --------------------------------------------------------------------------- #


class Document(BaseModel):
    id: str
    path: str
    title: str | None = None
    updated_at: str


class DocumentUpdate(BaseModel):
    body: str
    message: str  # commit message


# --------------------------------------------------------------------------- #
# Requests                                                                    #
# --------------------------------------------------------------------------- #


class PutDocumentRequest(BaseModel):
    path: str = Field(min_length=1)
    body: str = ""
    base_sha: str | None = None
    # On create, the template the page was started from — seeds the page's
    # update policy from that template (auto-update default + update
    # instruction). Ignored when editing an existing page.
    template_id: str | None = None


class CreateFolderRequest(BaseModel):
    path: str = Field(min_length=1)


class MovePathRequest(BaseModel):
    old_path: str = Field(min_length=1)
    new_path: str = Field(min_length=1)


class ReindexRequest(BaseModel):
    path: str = Field(min_length=1)


class ReviseDraftRequest(BaseModel):
    """Live edit of an unsaved draft from the drafting chat. ``body`` is the
    current editor content (may be empty); ``instruction`` is what to change."""

    body: str
    instruction: str = Field(min_length=1)


class ReviseDraftResponse(BaseModel):
    """The full revised document body to drop back into the editor."""

    body: str


class IngestRequest(BaseModel):
    """Inbound document push from external systems (e.g. Onyx connectors)."""

    model_config = {"populate_by_name": True}

    content: str = Field(min_length=1)
    title: str | None = None
    source: str | None = None
    source_document_id: str | None = Field(default=None, alias="document_id")
    url: str | None = None
    metadata: dict[str, Any] | None = None
    updated_at: str | None = None
    diff: str | None = None


# --------------------------------------------------------------------------- #
# Responses                                                                   #
# --------------------------------------------------------------------------- #


class DocumentEntry(BaseModel):
    path: str
    updated_at: str  # ISO-8601 author-time of the most recent commit touching the path
    id: str | None = None  # stable wiki_doc_id; None for pre-id rows not yet backfilled


class ListDocumentsResponse(BaseModel):
    entries: list[DocumentEntry]


class DocRef(BaseModel):
    """A path paired with its stable id, for id-based navigation links."""

    path: str
    id: str | None = None


class RecordRecentDocRequest(BaseModel):
    path: str


class RecentDocsResponse(BaseModel):
    # Newest-first paths of docs the user opened, already filtered to
    # ones that still exist and remain readable.
    paths: list[str]
    # Same list paired with stable ids, for id-based links. Kept alongside
    # ``paths`` (not replacing it) so the pre-migration frontend still works.
    items: list[DocRef] = []


class StarDocRequest(BaseModel):
    path: str


class ReorderStarredRequest(BaseModel):
    # The full starred list in the user's new order.
    paths: list[str]


class StarredDocsResponse(BaseModel):
    # User-ordered starred doc paths, already filtered to ones that
    # still exist and remain readable.
    paths: list[str]
    # Same list paired with stable ids, for id-based links. Additive; see
    # RecentDocsResponse.
    items: list[DocRef] = []


class RecentPageView(BaseModel):
    """A recently-updated page for the home-page "Recent Pages" grid.

    ``title`` is the doc's leading ``# H1`` when present, else the
    filename. ``preview`` is the body with frontmatter and that leading
    heading stripped — the card renders it as masked markdown.
    """

    path: str
    title: str
    updated_at: str
    preview: str
    id: str | None = None  # stable wiki_doc_id


class ListRecentPagesResponse(BaseModel):
    pages: list[RecentPageView]


class GetDocumentResponse(BaseModel):
    path: str
    body: str
    head_sha: str | None
    ref: str | None = None  # only set when reading at a specific ref
    id: str | None = None  # stable wiki_doc_id; None for historical/deleted reads
    attribution: Attribution | None = None
    sources: list[SourceRef] = Field(default_factory=list)
    # Whether the caller may edit this page. Joining the co-edit session is
    # read-gated (page-open presence), so the frontend uses this to suppress
    # ops/cursors and render read-only affordances for read-only viewers.
    # Required (no default): every constructor must resolve it from the ACL,
    # so a new call site can't silently advertise write access.
    can_write: bool


class PutDocumentResponse(BaseModel):
    path: str
    sha: str
    created: bool
    deprecated: list[str]
    id: str | None = None  # stable wiki_doc_id (minted on create, backfilled on edit)


class ResolveDocIdResponse(BaseModel):
    """A stable doc id resolved to its current binding.

    ``deleted_at`` set means the page/folder was deleted — the path is where
    it lived at delete time (feed it to the tombstone/restore endpoints).
    """

    id: str
    path: str
    kind: PageKind
    deleted_at: str | None = None


class ResolveIdsRequest(BaseModel):
    """Bulk path→id lookup. The frontend uses this to build id-based hrefs for
    paths it holds but has no id for yet — folder paths (not carried by the
    file-based tree listing) and synthesized breadcrumb ancestors."""

    # Bounded like the other parameterised endpoints; a single view resolves at
    # most a handful (visible folders + breadcrumb ancestors), so 1000 is ample.
    paths: list[str] = Field(max_length=1000)


class ResolveIdsResponse(BaseModel):
    # One entry per input path that has a live id row; paths without one are
    # simply omitted (the caller falls back to a path URL).
    items: list[DocRef]


class CreateFolderResponse(BaseModel):
    path: str
    sha: str


class MovedFile(BaseModel):
    old: str
    new: str


class MovePathResponse(BaseModel):
    old_path: str
    new_path: str
    sha: str
    moved: list[MovedFile]


class DeleteDocumentResponse(BaseModel):
    sha: str
    # Deleting moves the item to Trash; this is its handle for restore/undo.
    trash_id: str | None = None


class TrashEntryView(BaseModel):
    """One item in the Trash list."""

    trash_id: str
    path: str  # original location; restore moves it back here
    kind: PageKind
    trashed_by: str
    trashed_at: str  # ISO-8601
    can_restore: bool = False


class TrashListResponse(BaseModel):
    items: list[TrashEntryView]


class TrashItemView(TrashEntryView):
    """A single trashed item plus (for a page) its content, for preview."""

    body: str | None = None


class RestoreTrashRequest(BaseModel):
    trash_id: str = Field(min_length=1)


class RestorePathResponse(BaseModel):
    path: str  # where it was restored to
    sha: str
    restored: list[str]  # every file reintroduced


class PurgeTrashResponse(BaseModel):
    trash_id: str
    path: str  # the original path that was permanently removed


class ReindexResponse(BaseModel):
    path: str
    queued: bool


class IngestResponse(BaseModel):
    queued: bool
    task_id: str | None


class IngestTooLargeResponse(BaseModel):
    """413 response when an ingest payload exceeds ``max_doc_chars``."""

    error: str
    limit: int
    received: int


class CommitView(BaseModel):
    sha: str
    author: str
    ts: str
    message: str
    body: str = ""
    added: int = 0
    removed: int = 0
    triggered: int = 0  # number of automations this commit fired
    attribution: Attribution | None = None


class FileHistoryResponse(BaseModel):
    path: str
    head_sha: str | None
    commits: list[CommitView]


class WordDiff(BaseModel):
    """A 1-remove/1-add line collapsed into one rendered row.

    Common leading + trailing word tokens become `prefix`/`suffix`;
    the middle is split into struck-through `removed` and green `added`.
    """

    prefix: str
    removed: str
    added: str
    suffix: str


class DiffLine(BaseModel):
    """One rendered row in a diff hunk.

    Field population by `kind`:
    - `context` / `add` / `remove`: `text` set, `word_diff` None.
    - `word`: `word_diff` set, `text` None.
    - `context` and `word`: both `old_lineno` and `new_lineno` set.
    - `add`: only `new_lineno` set. `remove`: only `old_lineno` set.
    """

    kind: Literal["context", "add", "remove", "word"]
    text: str | None
    word_diff: WordDiff | None
    old_lineno: int | None
    new_lineno: int | None


class DiffHunk(BaseModel):
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLine]


class FileDiffResponse(BaseModel):
    path: str
    sha: str
    parent_sha: str | None
    hunks: list[DiffHunk]
    is_creation: bool


class SearchHitView(BaseModel):
    doc_id: str  # search-index key (currently the path); distinct from the stable id below
    path: str
    title: str | None
    snippet: str
    score: float
    id: str | None = None  # stable wiki_doc_id, for id-based navigation


class FolderHitView(BaseModel):
    path: str
    id: str | None = None  # stable wiki_doc_id of the folder, for id-based navigation


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHitView]
    folders: list[FolderHitView] = []


# --------------------------------------------------------------------------- #
# Agent activity                                                              #
# --------------------------------------------------------------------------- #


class ActivityRowView(BaseModel):
    """One active registration on a doc, as the UI and agents see it.

    Mirror of ``app.wiki.agent_activity.ActivityRow`` minus the internal
    ``id`` and the ``doc_path`` the response carries once. ``user_id``
    ties the agent to the user it acts for. ``owner_display`` is the
    user's display name, falling back to email. ``agent_name`` is
    ``None`` when the agent didn't identify itself.
    """

    user_id: str
    owner_display: str
    agent_name: str | None
    activity: str  # "read" | "wrote"
    description: str | None
    registered_at: str
    expires_at: str
    agent_session_id: str | None


class DocumentActivityResponse(BaseModel):
    path: str
    agents: list[ActivityRowView]


# --------------------------------------------------------------------------- #
# Document drafting (template-seeded pages)                                   #
# --------------------------------------------------------------------------- #


class DocumentDraftView(BaseModel):
    """Active "drafting from template" state for a wiki page."""

    path: str
    template_id: str
    template_name: str | None
    system_prompt: str | None
    created_at: str


class SetDocumentDraftRequest(BaseModel):
    """Body for ``POST /api/wiki/file/draft``.

    ``template_id=None`` clears the draft row. Otherwise upserts: the
    template's current body becomes the divergence snapshot.
    """

    path: str = Field(min_length=1)
    template_id: str | None = None
