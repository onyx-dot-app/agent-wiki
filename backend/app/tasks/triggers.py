"""Post-commit trigger fan-out.

After a successful ``commit_file`` on a wiki doc, the API enqueues
``fan_out_trigger_eval`` here. It loads BEFORE/AFTER from git, finds the
``delta`` triggers attached to the doc and its parent dirs, runs each
through the NL evaluator, and writes one ``trigger.fire`` event per match.

V0 records events only — no outbound dispatch (see
``local_data/wiki/natural-language-triggers``).
"""
from __future__ import annotations

import json
import subprocess

from app.db.sqlite import connect
from app.tasks.huey_app import huey
from app.triggers import diff as diff_helper
from app.triggers.engine import evaluate_delta, find_matching_triggers
from app.wiki import git as wiki_git


@huey.task()
def fan_out_trigger_eval(
    doc_path: str,
    sha: str,
    change_kind: str,
    actor: str | None = None,
) -> None:
    triggers = find_matching_triggers(doc_path)
    if not triggers:
        return

    after = _read_at(sha, doc_path)
    before = _read_at(f"{sha}^", doc_path)

    before_snippet, after_snippet = diff_helper.build_payload(
        before, after, change_kind=change_kind
    )

    for trigger in triggers:
        matched, reason = evaluate_delta(
            trigger, before_snippet, after_snippet, change_kind=change_kind
        )
        if not matched:
            continue
        _record_fire(
            trigger_id=trigger["id"],
            doc_path=doc_path,
            sha=sha,
            change_kind=change_kind,
            reason=reason,
            actor=actor,
        )


def _read_at(ref: str, rel_path: str) -> str:
    """Read ``rel_path`` at git ``ref``. Empty string if missing or parent-less."""
    try:
        return wiki_git.read_file(rel_path, ref=ref)
    except subprocess.CalledProcessError:
        return ""


def _record_fire(
    *,
    trigger_id: str,
    doc_path: str,
    sha: str,
    change_kind: str,
    reason: str,
    actor: str | None,
) -> None:
    payload = json.dumps(
        {
            "trigger_id": trigger_id,
            "doc_path": doc_path,
            "sha": sha,
            "change_kind": change_kind,
            "reason": reason,
        }
    )
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO events(kind, actor, target, payload_json) VALUES (?, ?, ?, ?)",
            ("trigger.fire", actor, trigger_id, payload),
        )
    finally:
        conn.close()
