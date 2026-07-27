"""Handler for the `write_doc` tool. Spec lives in `write_doc.json`.

Full-body overwrite (or create). Use sparingly — prefer ``edit_doc`` for
surgical changes.
"""
from __future__ import annotations

from typing import Any

import logging

from app.auth import current_user
from app.wiki import templates as templates_repo
from app.wiki import update_policy
from app.wiki import utils as wiki_utils
from app.wiki import git as wiki_git
from app.wiki.automanage import preflight
from app.llm.agents.tools.errors import ToolError
from app.llm.errors import LLMError
from app.models.wiki import ChangeKind, CommitMaxRetriesError

log = logging.getLogger(__name__)


def _seed_create_policy(
    path: str,
    template_id: str | None,
    update_instruction: str | None,
    auto_update_disabled: bool | None,
) -> str | None:
    """Set a new page's update policy. Seed from the template the agent picked
    (else the Blank default), then apply the agent's explicit overrides — which
    win, since the update instruction is how the agent scopes the page.

    Returns a warning string if a *requested* template vanished between
    validation and here (the page is already committed, so the create still
    succeeds, but its template policy wasn't applied) — else None."""
    user = current_user()
    actor = user.id if user else None
    warning: str | None = None
    tid = template_id or templates_repo.blank_template_id()
    if tid and not templates_repo.apply_policy_to_page(path, tid, actor):
        # The template was deleted after the up-front check; don't pretend the
        # policy applied. Only warn for an explicitly requested template — the
        # Blank-default being absent is an expected no-op.
        if template_id:
            warning = (
                "template was deleted before its policy could be applied; the page "
                "uses the default update policy — set it with set_update_policy"
            )
    overrides: dict[str, Any] = {}
    if isinstance(update_instruction, str):
        overrides["update_instruction"] = update_instruction or None
    if isinstance(auto_update_disabled, bool):
        overrides["ingestion_auto_update_disabled"] = auto_update_disabled
    if overrides:
        update_policy.set_policy(path, actor_user_id=actor, **overrides)
    return warning


def handle(args: dict[str, Any]) -> Any:
    try:
        path = wiki_utils.validate_doc_path(args.get("path"))
        body = args.get("body")
        commit_message = args.get("commit_message")
        base_sha = args.get("base_sha")
        activity_ttl = wiki_utils.parse_expires_in_seconds(args.get("expires_in_seconds"))
        if not isinstance(body, str):
            raise ToolError("body is required (string)")
        if not isinstance(commit_message, str) or not commit_message.strip():
            raise ToolError("commit_message is required")
        if base_sha is not None and not isinstance(base_sha, str):
            raise ToolError("base_sha must be a string when provided")
        template_id = args.get("template_id")
        if template_id is not None and not isinstance(template_id, str):
            raise ToolError("template_id must be a string when provided")
        # Create-time policy overrides — the agent's chance to scope the page.
        update_instruction = args.get("update_instruction")
        if update_instruction is not None and not isinstance(update_instruction, str):
            raise ToolError("update_instruction must be a string when provided")
        auto_update_disabled = args.get("ingestion_auto_update_disabled")
        if auto_update_disabled is not None and not isinstance(auto_update_disabled, bool):
            raise ToolError("ingestion_auto_update_disabled must be a boolean when provided")

        existed = wiki_utils.file_exists(path)
        # Validate a create-from-template id up front — before any commit — so a
        # bad id fails the call instead of creating a page with no policy.
        if not existed and template_id and templates_repo.get(template_id) is None:
            return {
                "error": "template_not_found",
                "message": "template_id does not match any template; call list_templates.",
            }
        if existed:
            # Full-body overwrite requires base_sha so we can 3-way merge
            # if a concurrent commit landed between when the agent read the
            # doc and when it calls write_doc.
            if base_sha is None:
                return {
                    "error": "base_sha_required_for_overwrite",
                    "message": (
                        "write_doc on an existing file requires base_sha "
                        "(the sha you last read). Re-read the doc and "
                        "pass its sha as base_sha."
                    ),
                }
            # base_sha is the merge base: the 3-way merge inside
            # commit_and_fan_out reconciles drift against it, so it must
            # resolve to a real commit.
            try:
                base_body = wiki_git.read_file(path, ref=base_sha)
            except wiki_git.UnknownSha:
                return {
                    "error": "base_sha_not_found",
                    "message": (
                        "base_sha does not resolve to a known commit; "
                        "re-read the doc and pass its sha as base_sha."
                    ),
                }
            try:
                result = wiki_utils.commit_and_fan_out(
                    path=path, body=body, message=commit_message.strip(),
                    change_kind=ChangeKind.EDIT,  # new files take the else branch below
                    base_body=base_body,
                    ai_merge=True,
                    activity_ttl=activity_ttl,
                )
            except CommitMaxRetriesError as exc:
                return {
                    "error": "stale_base",
                    "message": "concurrent edits kept landing; max retries exceeded",
                    "current_sha": exc.current_sha,
                }
            except LLMError as exc:
                return {"error": f"llm_error: {exc}"}
            if result is None:
                return {"path": path, "sha": wiki_git.head_sha_for_path(path), "no_change": True}
            return {
                "path": path,
                "sha": result.sha,
                "created": False,
                "diff": wiki_utils.unified_diff(result.old_body, result.new_body, path),
                "broken_links": wiki_utils.broken_links(path, result.new_body),
            }
        else:
            # Creation preflight — surface, never block: the create always
            # proceeds (the human owns the cleanup decision and may ignore
            # it), but instant-truth conflicts (case collision,
            # byte-identical duplicate) annotate the result so the agent can
            # tell the human, and Auto Organize's focused run turns the same
            # finding into the on-page proposal the human can act on.
            # Fail-open: a broken check never touches the write.
            try:
                conflicts = preflight.check_creation(path, body)
            except Exception:
                log.exception("creation preflight failed for %s", path)
                conflicts = []
            # New file: no base to merge against, so this always commits.
            result = wiki_utils.commit_and_fan_out(
                path=path, body=body, message=commit_message.strip(),
                change_kind=ChangeKind.CREATE, activity_ttl=activity_ttl,
            )
            if result is None:
                raise RuntimeError("commit_and_fan_out returned None on a no-base commit")
            warning = _seed_create_policy(
                path, template_id, update_instruction, auto_update_disabled
            )
            out: dict[str, Any] = {
                "path": path,
                "sha": result.sha,
                "created": True,
                "diff": wiki_utils.unified_diff("", body, path),
                "broken_links": wiki_utils.broken_links(path, body),
            }
            if conflicts:
                out["conflicts"] = [c.model_dump() for c in conflicts]
                out["message"] = (
                    "created, but note: "
                    + " ".join(c.suggestion for c in conflicts)
                    + " An Auto Organize suggestion will appear on the "
                    "page for the user to accept or ignore — mention this "
                    "to them."
                )
            if warning:
                out["warning"] = warning
            return out
    except ToolError as exc:
        return {"error": str(exc)}
