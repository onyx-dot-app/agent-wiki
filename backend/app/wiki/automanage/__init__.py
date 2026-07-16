"""Wiki Auto Management — AI-initiated structural cleanup of the wiki.

Detection (this package's ``detectors/`` seam + the runner) finds duplicates,
misplaced pages, and stale hierarchy and emits ``change_proposals``; a human
(or an AI-managed scope) approves before anything touches the wiki. Design:
``design/Wiki Auto Management — Engineering.md`` and its Detection sub-page.
"""
from __future__ import annotations
