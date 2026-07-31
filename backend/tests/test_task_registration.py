"""Every task module must be imported by the worker.

A handler only exists because its module was imported — ``@queue.task()`` registers on import.
So a module missing from ``run_worker._TASK_MODULES`` has no handler, and the worker's response
to an enqueued message it cannot handle is to log and DISCARD it (see ``queue._dispatch``). The
caller sees a successful enqueue and nothing ever runs.

That failure is invisible from either end, which is why it is pinned here rather than left to
review: ``app.tasks.entity_types`` shipped without registration and would have swallowed the
first production derivation.
"""

from __future__ import annotations

import ast
import pathlib

from app.tasks.run_worker import _TASK_MODULES

_TASKS_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "tasks"
# Infrastructure, not task definitions: these define the queues and the consumer itself.
_NOT_TASK_MODULES = {"__init__", "queue", "queues", "run_worker"}


def _defines_a_task(path: pathlib.Path) -> bool:
    """True if the module decorates anything with ``.task()`` / ``.periodic_task()``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            call = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(call, ast.Attribute) and call.attr in ("task", "periodic_task"):
                return True
    return False


def test_every_task_module_is_imported_by_the_worker() -> None:
    defining = {
        f"app.tasks.{path.stem}"
        for path in sorted(_TASKS_DIR.glob("*.py"))
        if path.stem not in _NOT_TASK_MODULES and _defines_a_task(path)
    }

    missing = defining - set(_TASK_MODULES)
    assert not missing, (
        f"task module(s) not imported by run_worker: {sorted(missing)} — their handlers are "
        "unregistered, so the worker would discard every enqueued message"
    )


def test_the_worker_imports_nothing_that_no_longer_exists() -> None:
    """A stale entry raises ImportError at worker startup, taking down every queue."""
    for module in _TASK_MODULES:
        assert (_TASKS_DIR / f"{module.rsplit('.', 1)[1]}.py").exists(), module
