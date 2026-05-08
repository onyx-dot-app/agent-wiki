"""Read-only bash execution for the chat agent.

Two layers (mirroring opencode and EnterpriseRAG-Bench's ``agent_retrieval``):

* **Execution** — ``execute_chain`` runs a (possibly piped) shell command
  against the wiki working tree. Each segment is a fresh ``subprocess.run``;
  no persistent shell, no exported-var carry-over, no working-directory
  drift across calls. Pipes / ``&&`` / ``||`` / ``;`` semantics are honored
  by inspecting return codes between segments.

* **Presentation** — ``format_output`` truncates and search-caps the result
  for LLM consumption. Generic cap is 2 000 lines or 50 KB (whichever hits
  first); a tighter 100-line cap kicks in when the *last* segment is a
  search/glob command (``grep``, ``rg``, ``find``).

The tool is **read-only by design**: an allowlist of safe Unix commands
(``ALLOWED_COMMANDS``) is the entire policy. Writes to the wiki must go
through ``write_doc`` / ``edit_doc`` / ``multi_edit`` so they're approved
and committed via ``app/wiki/git.py``. Anything that could mutate state
(``rm``, ``mv``, ``git``, ``sh``, redirects, ``$(...)``, etc.) is either
not in the allowlist or restricted by the first-token check.

Adapted from ``EnterpriseRAG-Bench/src/scripts/answer_generation/agent_retrieval.py``
(L427–622). Skipped from the original: per-session repeat-detection,
zero-result subdir hints, semaphore-based concurrency gate, deadline
crediting — all of which were tuned for that project's batch eval loop.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass

from app import config as app_config

# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #

# Read-only commands only. No `rm`, `mv`, `cp`, `git`, `sh`, `bash`, `python`,
# no shell redirection writers — see module docstring for rationale.
ALLOWED_COMMANDS: frozenset[str] = frozenset(
    {
        "cat",
        "find",
        "grep",
        "ls",
        "head",
        "tail",
        "wc",
    }
)

# Per-segment timeout. The chain as a whole can run up to N * timeout,
# but in practice failures abort early.
COMMAND_TIMEOUT_SECONDS = 30

# Generic output truncation. 2 000 lines or 50 KB, whichever is hit first.
TRUNCATION_MAX_LINES = 2_000
TRUNCATION_MAX_CHARS = 50_000

# Tighter cap when the *last* segment is a search command.
SEARCH_RESULT_MAX_LINES = 100
_SEARCH_COMMANDS: frozenset[str] = frozenset({"grep", "find"})


# --------------------------------------------------------------------------- #
# Errors / data                                                               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ChainSegment:
    """One link in a piped command chain. ``operator`` is the symbol
    *following* the segment (``|``, ``&&``, ``||``, ``;``); ``None`` for the
    final segment."""

    command: str
    operator: str | None


@dataclass(frozen=True)
class BashResult:
    output: str
    exit_code: int
    elapsed_ms: int
    truncated: bool


# --------------------------------------------------------------------------- #
# Chain parsing                                                               #
# --------------------------------------------------------------------------- #


def parse_chain(command_string: str) -> list[ChainSegment]:
    """Split a shell command string into segments, respecting quotes."""
    segments: list[ChainSegment] = []
    current: list[str] = []
    i = 0
    n = len(command_string)

    while i < n:
        ch = command_string[i]

        # Quoted regions pass through untouched (so `grep '|' file` doesn't
        # split on the literal pipe).
        if ch in ('"', "'"):
            quote_char = ch
            current.append(ch)
            i += 1
            while i < n and command_string[i] != quote_char:
                if command_string[i] == "\\" and i + 1 < n:
                    current.append(command_string[i])
                    current.append(command_string[i + 1])
                    i += 2
                else:
                    current.append(command_string[i])
                    i += 1
            if i < n:
                current.append(command_string[i])
                i += 1
            continue

        if i + 1 < n:
            two = command_string[i : i + 2]
            if two in ("&&", "||"):
                segments.append(ChainSegment("".join(current).strip(), two))
                current = []
                i += 2
                continue

        if ch == "|":
            segments.append(ChainSegment("".join(current).strip(), "|"))
            current = []
            i += 1
            continue

        if ch == ";":
            segments.append(ChainSegment("".join(current).strip(), ";"))
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    remaining = "".join(current).strip()
    if remaining:
        segments.append(ChainSegment(remaining, None))
    return [s for s in segments if s.command.strip()]


# --------------------------------------------------------------------------- #
# Allowlist                                                                   #
# --------------------------------------------------------------------------- #


def _validate_segment(command: str) -> str | None:
    """Return an error message if the first token of ``command`` is not in
    ``ALLOWED_COMMANDS``. Used as the upfront gate during parsing — every
    segment of the chain is checked before any subprocess fires.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return f"[error] could not parse command: {command!r}"
    if not tokens:
        return None
    cmd_name = os.path.basename(tokens[0])
    if cmd_name not in ALLOWED_COMMANDS:
        allowed = ", ".join(sorted(ALLOWED_COMMANDS))
        return (
            f"[error] command {cmd_name!r} is not allowed. "
            f"Allowed commands: {allowed}. "
            "Writes to the wiki go through edit_doc / write_doc / multi_edit."
        )
    return None


def validate_chain(segments: list[ChainSegment]) -> str | None:
    """Run the allowlist check against every parsed segment up front.

    Returns the first error message found, or ``None`` if the whole chain
    is allowed. Called once, immediately after parsing, before any
    subprocess runs.
    """
    for seg in segments:
        err = _validate_segment(seg.command)
        if err:
            return err
    return None


# --------------------------------------------------------------------------- #
# Execution                                                                   #
# --------------------------------------------------------------------------- #


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data


def execute_chain(
    command_string: str, *, cwd: str | None = None
) -> tuple[str, int, float]:
    """Run a (possibly piped) chain of allowlisted commands.

    Returns ``(output, exit_code, elapsed_ms)``. ``cwd`` defaults to
    ``CONFIG.wiki_dir`` so the model explores the wiki working tree, not
    the Flask source.
    """
    t0 = time.monotonic()
    # Look up CONFIG.wiki_dir at call time so test fixtures that patch
    # ``app.config.CONFIG`` flow through (the module-level alias would have
    # been bound to the original Config at import time).
    cwd = cwd or app_config.CONFIG.wiki_dir

    segments = parse_chain(command_string)
    if not segments:
        elapsed = (time.monotonic() - t0) * 1000
        allowed = ", ".join(sorted(ALLOWED_COMMANDS))
        return (
            f"[error] empty command — available: {allowed}",
            1,
            elapsed,
        )

    # Upfront allowlist gate: every segment is checked before any
    # subprocess fires. Pipes can't smuggle a disallowed command.
    err = validate_chain(segments)
    if err:
        elapsed = (time.monotonic() - t0) * 1000
        return (err, 1, elapsed)

    stdin_data: bytes | None = None
    last_stdout: bytes = b""
    last_returncode: int = 0

    i = 0
    while i < len(segments):
        seg = segments[i]
        try:
            proc = subprocess.run(
                seg.command,
                shell=True,
                input=stdin_data,
                capture_output=True,
                cwd=cwd,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            elapsed = (time.monotonic() - t0) * 1000
            return (
                f"[error] command timed out after {COMMAND_TIMEOUT_SECONDS} seconds. "
                "Try narrowing the search: scope content search to a specific "
                "subdirectory or use `find -name` for filename discovery.",
                1,
                elapsed,
            )
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return (f"[error] command failed: {exc}", 1, elapsed)

        stdout = proc.stdout
        stderr = proc.stderr
        rc = proc.returncode

        # Binary detection only on the *final* output — intermediate pipe
        # segments may legitimately contain null bytes.
        is_last = seg.operator is None
        if is_last and _is_binary(stdout):
            elapsed = (time.monotonic() - t0) * 1000
            return ("[error] binary file detected.", 1, elapsed)

        # Non-zero rc *with* stderr is a real error and aborts the chain.
        # Non-zero rc *without* stderr (e.g. `grep` no-match → rc=1) is normal.
        if rc != 0 and stderr:
            elapsed = (time.monotonic() - t0) * 1000
            error_output = stderr.decode("utf-8", errors="replace").strip()
            return (f"[stderr] {error_output}", rc, elapsed)

        operator = seg.operator
        if operator == "|":
            stdin_data = stdout
        elif operator == "&&":
            if rc != 0:
                last_stdout = stdout
                last_returncode = rc
                break
            stdin_data = None
        elif operator == ";":
            stdin_data = None
        elif operator == "||":
            if rc == 0:
                # `||` short-circuits when the lhs succeeded — keep its
                # output as the chain result. (The upstream port loses
                # this case; we save it explicitly.)
                last_stdout = stdout
                last_returncode = rc
                break
            stdin_data = None
        else:
            last_stdout = stdout
            last_returncode = rc
            break

        last_stdout = stdout
        last_returncode = rc
        i += 1

    elapsed = (time.monotonic() - t0) * 1000
    decoded = last_stdout.decode("utf-8", errors="replace")
    return (decoded, last_returncode, elapsed)


# --------------------------------------------------------------------------- #
# Presentation                                                                #
# --------------------------------------------------------------------------- #


def _apply_search_truncation(output: str, command_string: str) -> tuple[str, str]:
    """Cap output at 100 lines when the chain ends in a search command.

    Returns ``(possibly_truncated, hint)``; ``hint`` is empty when no
    truncation kicked in.
    """
    segments = parse_chain(command_string)
    if not segments:
        return output, ""

    last_cmd = segments[-1].command.strip()
    try:
        tokens = shlex.split(last_cmd)
    except ValueError:
        return output, ""
    if not tokens or os.path.basename(tokens[0]) not in _SEARCH_COMMANDS:
        return output, ""

    lines = output.splitlines(keepends=True)
    if len(lines) <= SEARCH_RESULT_MAX_LINES:
        return output, ""
    truncated = "".join(lines[:SEARCH_RESULT_MAX_LINES])
    dropped = len(lines) - SEARCH_RESULT_MAX_LINES
    return truncated, f"({dropped} more results truncated)"


def _apply_generic_truncation(output: str) -> tuple[str, str]:
    """Cap output at TRUNCATION_MAX_LINES / TRUNCATION_MAX_CHARS."""
    lines = output.splitlines(keepends=True)
    total_lines = len(lines)
    total_chars = len(output)

    too_many_lines = total_lines > TRUNCATION_MAX_LINES
    too_many_chars = total_chars > TRUNCATION_MAX_CHARS
    if not too_many_lines and not too_many_chars:
        return output, ""

    if too_many_lines:
        shown = "".join(lines[:TRUNCATION_MAX_LINES])
        if len(shown) > TRUNCATION_MAX_CHARS:
            shown = shown[:TRUNCATION_MAX_CHARS]
        desc = f"{total_lines} lines, {total_chars} chars"
    else:
        shown = output[:TRUNCATION_MAX_CHARS]
        desc = f"{total_chars} chars"
    return shown, f"--- output truncated ({desc}) ---"


def format_output(raw: str, command: str) -> tuple[str, bool]:
    """Apply search-cap then generic truncation. Returns ``(text, truncated)``."""
    out, search_hint = _apply_search_truncation(raw, command)
    out, generic_hint = _apply_generic_truncation(out)

    parts: list[str] = [out]
    if search_hint:
        parts.append(search_hint)
    if generic_hint:
        parts.append(generic_hint)
    return "\n".join(p for p in parts if p), bool(search_hint or generic_hint)


# --------------------------------------------------------------------------- #
# Public entry point                                                          #
# --------------------------------------------------------------------------- #


def run(command: str, *, cwd: str | None = None) -> BashResult:
    """Run ``command`` and return a fully formatted result."""
    raw, rc, elapsed_ms = execute_chain(command, cwd=cwd)
    text, truncated = format_output(raw, command)
    return BashResult(
        output=text, exit_code=rc, elapsed_ms=int(elapsed_ms), truncated=truncated
    )
