"""The agentic proposal applier — one LLM-driven execution path for every op.

Lives with the other LLM agents; the automanage executor (its only caller)
owns the pre-gates and the post-run scope check/revert.

Op-specific knowledge lives in the *proposal* (op label, paths, summary,
instruction, ``proposed_bodies`` preview), not in per-op executor code: the
model reads the approved intent and applies it against the wiki's *current*
state with a small set of bounded tools. A new op kind therefore needs a
detector that authors a good proposal — and zero new execution code.

The model's discretion is capped by rails that live outside it:

- **Tool surface** — read/write/move/retire on the proposal's own paths only;
  every mutation is additive history, deletion only via trash-move. There is
  no force op to misuse.
- **Scope check (in the executor)** — after the run, the git diff since the
  pre-run HEAD must touch only the proposal's paths (or their trash
  locations); anything else reverts additively and the run is rejected.

The consent model is intent-level: the reviewer approved the described
change; ``proposed_bodies`` is a strong preview, not a byte contract, so the
model may fold in trivial drift that landed while the proposal sat pending.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.llm import client
from app.llm.prompts import load_prompt
from app.models.wiki import ChangeKind, PathMove
from app.wiki import git, notify, retire, trash
from app.wiki.filesystem import safe_rel_path

log = logging.getLogger(__name__)

# LLM turns before the run is abandoned. An apply is a handful of tool calls
# (read, write, retire); a model still going at the cap is lost, not thorough.
MAX_STEPS = 8

_SYSTEM_PROMPT = load_prompt("automanage_apply.system")


class ApplyOutcome(BaseModel):
    """What the run did, for the executor's rails to judge."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    detail: str


def _tools() -> list[dict[str, Any]]:
    path_prop = {"type": "string", "description": "Wiki-relative .md path"}
    return [
        {
            "name": "read_page",
            "description": "Read the current body of one of the proposal's pages.",
            "input_schema": {
                "type": "object",
                "properties": {"path": path_prop},
                "required": ["path"],
            },
        },
        {
            "name": "write_page",
            "description": "Replace the body of one of the proposal's pages "
            "(commits; additive history).",
            "input_schema": {
                "type": "object",
                "properties": {"path": path_prop, "body": {"type": "string"}},
                "required": ["path", "body"],
            },
        },
        {
            "name": "move_page",
            "description": "Move/rename a page between two of the proposal's paths.",
            "input_schema": {
                "type": "object",
                "properties": {"source": path_prop, "dest": path_prop},
                "required": ["source", "dest"],
            },
        },
        {
            "name": "trash_page",
            "description": "Remove one of the proposal's pages outright: "
            "trash-move (restorable), no surviving page to forward to.",
            "input_schema": {
                "type": "object",
                "properties": {"path": path_prop},
                "required": ["path"],
            },
        },
        {
            "name": "retire_page",
            "description": "Retire a redundant page into a surviving page: "
            "trash-move (restorable) + forward its stable id to the survivor.",
            "input_schema": {
                "type": "object",
                "properties": {"source": path_prop, "target": path_prop},
                "required": ["source", "target"],
            },
        },
    ]


def _render_proposal(p: dict[str, Any]) -> str:
    lines = [
        f"Operation: {p['op']}",
        f"Summary (what the reviewer approved): {p['summary']}",
        f"Source paths: {json.dumps(p['source_paths'])}",
        f"Target paths: {json.dumps(p['target_paths'])}",
    ]
    if p.get("instruction"):
        lines.append(f"Instruction: {p['instruction']}")
    previews: dict[str, str] = p.get("proposed_bodies") or {}
    for path, body in previews.items():
        lines.append(f"\nApproved preview of resulting {path!r}:\n---\n{body}\n---")
    return "\n".join(lines)


class _ToolBox:
    """Bounded tool handlers, closed over the proposal's paths.

    Two path rules, both from the proposal's own data: every touched path
    must be one of the proposal's paths (scope), and **target paths must
    survive** — targets are, by schema semantics, the surviving/receiving
    side of any op (merge target, move destination), so the destructive
    tools refuse them. The executor re-checks both after the run."""

    def __init__(
        self,
        sources: frozenset[str],
        targets: frozenset[str],
        author: str | None,
    ):
        self._allowed = sources | targets
        self._targets = targets
        self._author = author
        self.mutated = False

    def _check(self, *paths: str, consumes: tuple[str, ...] = ()) -> str | None:
        for raw in (*paths, *consumes):
            try:
                path = safe_rel_path(raw)
            except ValueError as e:
                return f"invalid path {raw!r}: {e}"
            if path not in self._allowed:
                return (
                    f"path {path!r} is outside this proposal's scope; allowed: "
                    f"{sorted(self._allowed)}"
                )
        for raw in consumes:
            if safe_rel_path(raw) in self._targets:
                return (
                    f"path {raw!r} is one of the proposal's target paths — "
                    "targets must survive the operation and cannot be "
                    "trashed, retired, or moved away"
                )
        return None

    def read_page(self, args: dict[str, Any]) -> Any:
        if err := self._check(args["path"]):
            return {"error": err}
        body = git.read_file_opt(args["path"])
        if body is None:
            return {"error": f"no page at {args['path']!r}"}
        return {"path": args["path"], "body": body}

    def write_page(self, args: dict[str, Any]) -> Any:
        if err := self._check(args["path"]):
            return {"error": err}
        path = args["path"]
        sha = git.commit_file(
            path, args["body"], f"automanage: update {path}", author=self._author
        )
        notify.after_doc_write(path, sha, ChangeKind.EDIT, self._author)
        self.mutated = True
        return {"path": path, "sha": sha}

    def move_page(self, args: dict[str, Any]) -> Any:
        if err := self._check(args["dest"], consumes=(args["source"],)):
            return {"error": err}
        src, dst = args["source"], args["dest"]
        sha, moves = git.move_path(
            src, dst, f"automanage: move {src} -> {dst}", author=self._author
        )
        notify.after_path_move(
            moves, sha, self._author, root_move=PathMove(old=src, new=dst)
        )
        self.mutated = True
        return {"source": src, "dest": dst, "sha": sha}

    def trash_page(self, args: dict[str, Any]) -> Any:
        if self._targets:
            return {
                "error": "this proposal has surviving target paths — a "
                "removed page's identity must forward to the survivor; use "
                "retire_page instead of trash_page"
            }
        if err := self._check(consumes=(args["path"],)):
            return {"error": err}
        path = args["path"]
        dest = trash.trash_location(trash.new_trash_id(), path)
        sha, moves = git.move_path(
            path, dest, trash.trash_commit_message(path), author=self._author
        )
        notify.after_doc_trashed(
            moves, sha, self._author, root_move=PathMove(old=path, new=dest)
        )
        self.mutated = True
        return {"trashed": path, "sha": sha}

    def retire_page(self, args: dict[str, Any]) -> Any:
        if err := self._check(args["target"], consumes=(args["source"],)):
            return {"error": err}
        sha = retire.retire_page(
            args["source"], args["target"], author=self._author
        )
        self.mutated = True
        return {"retired": args["source"], "survivor": args["target"], "sha": sha}

    def dispatch(self, name: str, args: dict[str, Any]) -> Any:
        handler: Callable[[dict[str, Any]], Any] | None = getattr(self, name, None)
        if handler is None or name.startswith("_"):
            return {"error": f"unknown tool {name!r}"}
        try:
            return handler(args)
        except Exception as e:  # surface to the model, don't kill the run
            log.exception("agentic tool %s failed", name)
            return {"error": str(e)}


def apply_proposal(p: dict[str, Any], *, author: str | None) -> ApplyOutcome:
    """Drive the LLM to apply an approved proposal. Pure orchestration — the
    executor owns the pre-gates and the post-run scope check/revert."""
    sources = frozenset(safe_rel_path(x) for x in p["source_paths"])
    targets = frozenset(safe_rel_path(x) for x in p["target_paths"])
    box = _ToolBox(sources - targets, targets, author)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _render_proposal(p)},
    ]
    for _ in range(MAX_STEPS):
        result = client.complete(messages, tools=_tools())
        turn: dict[str, Any] = {"role": "assistant", "content": result.text}
        if result.tool_calls:
            turn["tool_calls"] = [
                {"id": c.id, "name": c.name, "arguments": c.arguments}
                for c in result.tool_calls
            ]
        messages.append(turn)
        if not result.tool_calls:
            text = result.text.strip()
            if text.upper().startswith("CANNOT APPLY"):
                return ApplyOutcome(ok=False, detail=text)
            if not box.mutated:
                # Finished without changing anything and without declaring
                # failure — treat as not applied rather than silently done.
                return ApplyOutcome(
                    ok=False, detail=f"no changes were made: {text or 'no summary'}"
                )
            return ApplyOutcome(ok=True, detail=text or "applied")
        for call in result.tool_calls:
            out = box.dispatch(call.name, call.arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": out if isinstance(out, str) else json.dumps(out),
                }
            )
    return ApplyOutcome(
        ok=False, detail=f"did not finish within {MAX_STEPS} steps"
    )
