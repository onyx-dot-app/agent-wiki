"""Batch LLM-as-judge for irrelevant samples in a JSONL eval file.

Usage:
    ANTHROPIC_API_KEY=... python judge_batch.py <file> --outcome irrelevant --n 100
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import anthropic

SYSTEM = (
    "You are an expert technical editor evaluating whether a wiki page should be updated "
    "based on a source document. Wiki pages are scoped and concise — more detail is not "
    "always better. Reply only with the requested JSON."
)

LABEL_LIST = [
    "committed_critical",
    "committed_moderate",
    "committed_nit",
    "no_change_covered",
    "no_change_extra",
    "irrelevant",
]

VALID_LABELS = set(LABEL_LIST)


def build_user_msg(sample: dict) -> str:
    src = (sample.get("source_content") or "")[:6000]
    wiki = (sample.get("wiki_body_before") or "")[:6000]
    label_list = ", ".join(f'"{l}"' for l in LABEL_LIST)
    return "\n\n".join([
        f"<source_document>\n{src}\n</source_document>",
        f"<wiki_page>\n{wiki}\n</wiki_page>",
        (
            "Given the source document and the wiki page above, decide what the correct "
            "reconciler action should be.\n\n"
            "First: scan the source for any specific fact, status update, or implicit action "
            "item that applies to this page's exact subject — even if the source is primarily "
            "about something else. A source that is mostly off-topic but contains one directly "
            "relevant fact should be judged on that fact alone.\n\n"
            "Wiki pages are intentionally scoped and concise. An update is only justified when "
            "the source contains information that clearly fits within the wiki page's existing "
            "purpose and level of detail. Avoid committing updates that would add excessive "
            "detail, transform the character of the page, or duplicate content the page "
            "already covers.\n\n"
            f"Labels: {label_list}\n\n"
            "- committed_critical: source contains a key fact, correction, or addition that "
            "clearly belongs in this wiki page and is missing\n"
            "- committed_moderate: source adds a concrete, focused piece of information that "
            "fits the page's scope\n"
            "- committed_nit: source only justifies a very minor or cosmetic touch\n"
            "- no_change_covered: wiki already covers the topic adequately — no new information "
            "in the source\n"
            "- no_change_extra: source is on-topic but the detail it adds exceeds the page's "
            "scope or level of detail\n"
            "- irrelevant: the source contains nothing — even in passing — that applies to "
            "this page\n\n"
            "Important distinctions:\n"
            "- Use no_change_covered or no_change_extra when the source is topically related "
            "but adds nothing useful. Reserve irrelevant for sources with no connection to the "
            "page's topic whatsoever.\n"
            "- A source about a different product that uses the same technology is not relevant "
            "— the page must cover the same specific system.\n"
            "- A conversational source (Slack, chat) that surfaces a potential issue or cc's "
            "team members tracked on the page implies a follow-up action and can qualify as "
            "committed_moderate.\n"
            "- Before labeling committed, check whether the wiki already has an entry for the "
            "specific deal, contact, or task the source describes. If it does and the source "
            "adds no materially new facts, use no_change_covered.\n"
            "- 'Tracked on the page' means the person has their own dedicated section as a "
            "primary subject. Being listed as an internal call attendee does not qualify.\n"
            "- If the source contains a Stage or Status field set to a terminal value "
            "(closedlost, closed, expired, cancelled, resolved, churned), the work is over "
            "— do not commit action items from it even if the document also contains call "
            "notes with next steps.\n\n"
            "When in doubt between committing and no_change, prefer no_change_extra.\n\n"
            'Respond with JSON only: {"label":"<one of the labels>","reasoning":"<1-2 sentences>"}'
        ),
    ])


def judge_sample(client: anthropic.Anthropic, sample: dict) -> tuple[str, str]:
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=256,
        system=SYSTEM,
        messages=[{"role": "user", "content": build_user_msg(sample)}],
    )
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    parsed = json.loads(text)
    label = parsed["label"]
    if label not in VALID_LABELS:
        raise ValueError(f"Unknown label: {label!r}")
    return label, parsed.get("reasoning", "")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="Path to JSONL eval file")
    parser.add_argument("--outcome", default="irrelevant", help="Filter by outcome")
    parser.add_argument("--n", type=int, default=100, help="How many to judge")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ANTHROPIC_API_KEY not set")

    path = Path(args.file)
    samples = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    by_id: dict[int, int] = {s["id"]: i for i, s in enumerate(samples)}

    candidates = [
        s for s in samples
        if s.get("outcome") == args.outcome and not s.get("judge_label")
    ]
    print(f"Candidates (outcome={args.outcome}, unjudged): {len(candidates)}")

    rng = random.Random(args.seed)
    to_judge = rng.sample(candidates, min(args.n, len(candidates)))
    print(f"Judging {len(to_judge)} samples…")

    client = anthropic.Anthropic(api_key=api_key)

    ok = mismatch = errors = 0
    for i, sample in enumerate(to_judge, 1):
        try:
            label, reasoning = judge_sample(client, sample)
            idx = by_id[sample["id"]]
            samples[idx]["judge_label"] = label
            samples[idx]["judge_reasoning"] = reasoning

            # Propagate to identical source+wiki pairs (same logic as eval_labeler)
            for j, s in enumerate(samples):
                if j == idx or s.get("judge_label"):
                    continue
                if (s.get("source_content") == sample.get("source_content") and
                        s.get("wiki_body_before") == sample.get("wiki_body_before")):
                    samples[j]["judge_label"] = label
                    samples[j]["judge_reasoning"] = reasoning

            match = label == args.outcome
            if not match:
                mismatch += 1
            else:
                ok += 1
            status = "✓" if match else f"✗ → {label}"
            print(f"[{i}/{len(to_judge)}] id={sample['id']} {status}  {reasoning[:80]}")
        except Exception as e:
            errors += 1
            print(f"[{i}/{len(to_judge)}] id={sample['id']} ERROR: {e}")

        # Write back after every sample so progress survives interruption
        path.write_text("\n".join(json.dumps(s) for s in samples) + "\n")

        if i < len(to_judge):
            time.sleep(0.3)

    print(f"\nDone. correct={ok}  mismatch={mismatch}  errors={errors}")
    if mismatch:
        mismatched = [
            s for s in samples
            if s.get("outcome") == args.outcome
            and s.get("judge_label")
            and s["judge_label"] != args.outcome
        ]
        print("\nMismatched samples:")
        for s in mismatched:
            print(f"  id={s['id']} judge={s['judge_label']}  {s.get('judge_reasoning','')[:100]}")


if __name__ == "__main__":
    main()
