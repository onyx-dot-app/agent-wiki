"""Slack integration: per-user named webhook registry + outbound client.

Backs the ``slack`` trigger destination. Each user owns named incoming
webhooks (``app/slack/webhooks.py``, managed from the Triggers page); a
trigger references one via ``Trigger.slack_webhook_id``. The dispatcher in
``app/tasks/triggers.py`` resolves that id to a URL and posts through
:func:`app.slack.client.post_message`.
"""
