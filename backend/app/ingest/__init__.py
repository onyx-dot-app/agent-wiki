"""Inbound document push from external systems (e.g. Onyx connectors).

The HTTP surface lives in ``app/api/documents.py`` (POST /api/documents/ingest).
Configuration (e.g. max document size) lives in ``app/ingest/settings.py``,
DB-backed and configured via the admin UI.
"""
