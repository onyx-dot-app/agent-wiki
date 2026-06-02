"""Slack integration: DB-backed webhook settings + outbound webhook client.

Backs the ``slack`` trigger destination. Settings are admin-managed at
``/admin/slack``; the dispatcher lives in ``app/tasks/triggers.py`` and posts
through :func:`app.slack.client.post_message`.
"""
