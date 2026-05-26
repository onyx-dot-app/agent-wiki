"""Mine wiki_updater eval cases from a Bo-style labeled ingest JSONL.

Bo's eval-labeler tool (``backend/scripts/eval_labeler.html``) emits a
JSONL file with one row per ``ingest_eval_samples`` record, augmented
with three label columns:

* ``outcome`` — what production actually shipped (raw reconciler).
* ``judge_label`` — LLM-as-judge call on the same row.
* ``label`` — Bo's human-corrected ground truth (this script's
  authoritative source).

This builder reads the JSONL, applies PII redaction, and writes one
``rd-real-<bucket>-<id>.yaml`` per kept row using ``label`` as the
expected class. The script can be re-run with each new labeled batch
Bo posts in #agent-wiki without restating any of the human work.

Usage:

    cd backend
    uv run python -m evals.datasets.wiki_updater._build_from_labeled_jsonl \\
        --input ~/Downloads/eval_samples_prod_1626_to_2893.jsonl \\
        --irrelevant-sample 100

``--irrelevant-sample N`` caps the heavy irrelevant tail to ``N`` cases
(stratified across ``source_type``) so the eval matrix stays bounded.
All committed/no-change cases are always kept — they're rare and
expensive to lose.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from evals.schema import TriggerClass, WikiUpdaterCase

log = logging.getLogger(__name__)


_BO_LABEL_TO_CLASS = {
    "irrelevant": TriggerClass.IRRELEVANT,
    "no_change_covered": TriggerClass.NO_CHANGE,
    "no_change_extra": TriggerClass.NO_CHANGE,
    "committed_moderate": TriggerClass.CHANGE,
}


_BO_LABEL_TO_BUCKET = {
    "irrelevant": "irrelevant",
    "no_change_covered": "nochange",
    "no_change_extra": "nochange",
    "committed_moderate": "committed",
}


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://[^\s>\)\]]+")
_AMOUNT_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?[KMB]\b|/(?:user|seat|month|year))?")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_LONG_DIGITS_RE = re.compile(r"\b\d{8,}\b")


def _redact(text: str) -> str:
    """Apply the same regex scrub used by the existing prod-mined cases.

    Order matters — URL must run before DOMAIN-style residues so that
    ``https://example.com`` collapses to a single ``<URL>``.
    """
    text = _EMAIL_RE.sub("<EMAIL>", text)
    text = _URL_RE.sub("<URL>", text)
    text = _AMOUNT_RE.sub("<AMOUNT>", text)
    text = _PHONE_RE.sub("<PHONE>", text)
    text = _LONG_DIGITS_RE.sub("<LONG_DIGITS>", text)
    return text


class _LiteralStr(str):
    """Marker subclass so PyYAML emits multi-line strings as ``|`` blocks."""


def _literal_representer(  # pyright: ignore[reportUnknownParameterType]
    dumper: yaml.SafeDumper, data: _LiteralStr
) -> yaml.ScalarNode:
    return dumper.represent_scalar(  # pyright: ignore[reportUnknownMemberType]
        "tag:yaml.org,2002:str", str(data), style="|"
    )


yaml.add_representer(_LiteralStr, _literal_representer, Dumper=yaml.SafeDumper)


def _as_yaml_str(s: str) -> _LiteralStr | str:
    """Return a literal-block string if there's a newline, else a plain str.

    PyYAML refuses ``|`` (literal) style when any line carries trailing
    whitespace or non-printable runs. Production text often has those, so
    rstrip each line — preserves the visible content + lets PyYAML pick
    the readable block form Bo/team will actually edit by hand.
    """
    if "\n" not in s:
        return s
    cleaned = "\n".join(line.rstrip() for line in s.splitlines())
    return _LiteralStr(cleaned)


def _row_to_case(row: dict[str, Any]) -> WikiUpdaterCase | None:
    """Translate one Bo-JSONL row to a ``WikiUpdaterCase``.

    Returns ``None`` if the row lacks the minimum fields a case needs —
    wiki_path + wiki_body_before + source_content + a recognized label.
    """
    label = (row.get("label") or "").strip()
    if label not in _BO_LABEL_TO_CLASS:
        return None
    wiki_path = row.get("wiki_path") or ""
    wiki_body = row.get("wiki_body_before") or ""
    source_content = row.get("source_content") or ""
    if not (wiki_path and wiki_body and source_content):
        return None

    expected_class = _BO_LABEL_TO_CLASS[label]
    bucket = _BO_LABEL_TO_BUCKET[label]
    case_id = "rd-real-%s-%05d" % (bucket, int(row["id"]))

    source_type = row.get("source_type") or "unknown"
    tags = [
        "real-world",
        "real-prod-decision",
        "human-verified",
        "source:%s" % source_type,
        "bo-label:%s" % label,
    ]

    doc_title = _redact(row.get("source_title") or "")
    doc_url_raw = row.get("source_url") or ""
    doc_url = "<URL>" if doc_url_raw else None
    doc_content = _redact(source_content)
    current_body = _redact(wiki_body)

    return WikiUpdaterCase(
        id=case_id,
        surface="reconcile_document",
        wiki_path=wiki_path,
        current_body=current_body,
        expected_class=expected_class,
        doc_title=doc_title or None,
        doc_url=doc_url,
        doc_content=doc_content,
        notes="Human-verified label (`%s`) from Bo's eval_labeler batch." % label,
        tags=tags,
    )


def _stratified_sample(cases: list[WikiUpdaterCase], *, n: int, seed: int) -> list[WikiUpdaterCase]:
    """Stratify by source-type tag, sample proportionally up to ``n`` total.

    Preserves the source-type distribution of the input set rather than
    over-weighting whichever bucket happens to dominate.
    """
    by_source: dict[str, list[WikiUpdaterCase]] = defaultdict(list)
    for c in cases:
        src = next((t for t in c.tags if t.startswith("source:")), "source:unknown")
        by_source[src].append(c)
    rng = random.Random(seed)
    out: list[WikiUpdaterCase] = []
    total = sum(len(v) for v in by_source.values())
    if total <= n:
        return list(cases)
    for src, group in by_source.items():
        share = max(1, round(n * len(group) / total))
        rng.shuffle(group)
        out.extend(group[:share])
    rng.shuffle(out)
    return out[:n]


def _case_to_yaml(case: WikiUpdaterCase) -> str:
    """Render a case as YAML with multi-line bodies as literal blocks."""
    data: dict[str, Any] = {
        "id": case.id,
        "surface": case.surface,
        "wiki_path": case.wiki_path,
        "current_body": _as_yaml_str(case.current_body),
        "expected_class": case.expected_class.value,
    }
    if case.doc_title is not None:
        data["doc_title"] = case.doc_title
    if case.doc_url is not None:
        data["doc_url"] = case.doc_url
    if case.doc_content is not None:
        data["doc_content"] = _as_yaml_str(case.doc_content)
    if case.notes:
        data["notes"] = case.notes
    if case.tags:
        data["tags"] = list(case.tags)
    return yaml.dump(data, Dumper=yaml.SafeDumper, sort_keys=False, allow_unicode=True)


def _scan_pii(text: str) -> list[str]:
    """Return labels for any PII patterns that survived the scrub."""
    findings: list[str] = []
    if _EMAIL_RE.search(text):
        findings.append("EMAIL")
    if _URL_RE.search(text):
        findings.append("URL")
    if _AMOUNT_RE.search(text):
        findings.append("AMOUNT")
    if _LONG_DIGITS_RE.search(text):
        findings.append("LONG_DIGITS")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Bo-style JSONL")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(__file__).parent / "cases",
        help="Output directory for YAML cases (default: cases/)",
    )
    parser.add_argument(
        "--irrelevant-sample",
        type=int,
        default=100,
        help="Cap on irrelevant cases (stratified by source_type). Default 100.",
    )
    parser.add_argument(
        "--seed", type=int, default=20260526, help="RNG seed for stratified sampling"
    )
    parser.add_argument(
        "--drop-existing-real",
        action="store_true",
        help=(
            "Delete prior rd-real-{nochange,irrelevant}-*.yaml cases first. "
            "Use when remediating an older mining run."
        ),
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv or sys.argv[1:])
    logging.basicConfig(level=args.log_level, format="%(levelname)s %(message)s")

    if not args.input.exists():
        log.error("input file not found: %s", args.input)
        return 2
    args.out_dir.mkdir(parents=True, exist_ok=True)

    all_cases: list[WikiUpdaterCase] = []
    skipped = 0
    for line in args.input.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            log.warning("skip malformed row: %s", exc)
            skipped += 1
            continue
        case = _row_to_case(row)
        if case is None:
            skipped += 1
            continue
        all_cases.append(case)

    if not all_cases:
        log.error("no usable rows in %s", args.input)
        return 2

    irrelevant = [c for c in all_cases if c.expected_class is TriggerClass.IRRELEVANT]
    keep_others = [c for c in all_cases if c.expected_class is not TriggerClass.IRRELEVANT]
    kept_irrelevant = _stratified_sample(irrelevant, n=args.irrelevant_sample, seed=args.seed)
    kept = sorted(keep_others + kept_irrelevant, key=lambda c: c.id)

    if args.drop_existing_real:
        for pattern in ("rd-real-nochange-*.yaml", "rd-real-irrelevant-*.yaml"):
            for p in args.out_dir.glob(pattern):
                p.unlink()
                log.info("dropped %s", p.name)

    written = 0
    pii_alarm = 0
    for case in kept:
        out_path = args.out_dir / ("%s.yaml" % case.id)
        rendered = _case_to_yaml(case)
        leak = _scan_pii(rendered)
        if leak:
            log.warning("PII residue %s in case %s; SKIPPED", leak, case.id)
            pii_alarm += 1
            continue
        out_path.write_text(rendered)
        written += 1

    log.info(
        "wrote %d cases (skipped %d input rows, %d PII alarms); irrelevant_kept=%d/%d, others=%d",
        written,
        skipped,
        pii_alarm,
        len(kept_irrelevant),
        len(irrelevant),
        len(keep_others),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
