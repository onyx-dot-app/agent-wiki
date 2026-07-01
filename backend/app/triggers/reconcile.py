"""One-time reconcile of legacy Slack triggers into destination configs.

Before the ``destination_configs`` registry, a trigger's Slack channel lived in
``slack_webhooks`` and was referenced by a top-level ``slack_webhook_id`` on the
trigger YAML. This mirrors each such channel into a ``destination_configs`` row
(once) and rewrites the trigger YAML to reference it by ``destination_config_id``.

Runs at boot before the cache rebuild. Already-reshaped triggers carry an
``actions`` list and are skipped, and each mirrored config is found by the
source-webhook marker rather than recreated, so it is a no-op after the first
boot.
"""
from __future__ import annotations

import logging
from typing import Any, cast

import yaml

from app.slack import webhooks as slack_webhooks
from app.triggers import destination_configs as dest_configs
from app.triggers import storage
from app.wiki import git as wiki_git

log = logging.getLogger(__name__)

# Marker in a mirrored config's config_json linking it to its source webhook, so
# the mirror is created exactly once.
_SOURCE_KEY = "from_slack_webhook"


def _mirror_config_id(webhook_id: str, owner_user_id: str) -> str | None:
    """The destination config mirroring ``webhook_id`` for its owner, created on
    first sight. None if the source webhook is gone or not owned."""
    for cfg in dest_configs.list_for_user(owner_user_id):
        config = cfg.get("config")
        if isinstance(config, dict) and cast(dict[str, Any], config).get(_SOURCE_KEY) == webhook_id:
            return cast(str, cfg["id"])
    hook = next(
        (w for w in slack_webhooks.list_for_user(owner_user_id) if w["id"] == webhook_id),
        None,
    )
    if hook is None:
        return None
    return cast(
        str,
        dest_configs.create(
            owner_user_id,
            type="slack",
            name=hook["name"],
            config={_SOURCE_KEY: webhook_id},
            secret=hook["webhook_url"],
        )["id"],
    )


def reconcile_legacy_slack_triggers() -> int:
    """Rewrite trigger YAML still on the legacy single-destination shape into the
    destination-config shape. Returns the number of files rewritten."""
    rewritten = 0
    for file_path in storage.list_all_files():
        try:
            loaded: object = yaml.safe_load(wiki_git.read_file(file_path))
        except Exception:
            log.warning("reconcile: unreadable trigger %s", file_path, exc_info=True)
            continue
        if not isinstance(loaded, dict) or "id" not in loaded or "actions" in loaded:
            continue  # not a trigger file, or already reshaped
        raw = cast(dict[str, Any], loaded)

        owner = raw.get("owner_user_id")
        webhook_id = raw.get("slack_webhook_id")
        config_id: str | None = None
        if (
            raw.get("destination") == "slack"
            and isinstance(webhook_id, str)
            and isinstance(owner, str)
        ):
            config_id = _mirror_config_id(webhook_id, owner)
            if config_id is None:
                # The trigger degrades to event-log only. Name the drop so
                # operators can find affected triggers after the migration.
                log.warning(
                    "reconcile: %s (owner %s) referenced slack webhook %s which no "
                    "longer exists; trigger falls back to event-log only",
                    file_path, owner, webhook_id,
                )

        reshaped = {
            k: v
            for k, v in raw.items()
            if k not in ("message", "destination", "slack_webhook_id")
        }
        reshaped["actions"] = [
            {"destination_config_id": config_id, "message": raw.get("message")}
        ]
        try:
            storage.write_trigger(reshaped, file_path=file_path, actor=None)
            rewritten += 1
        except Exception:
            log.exception("reconcile: failed to rewrite %s", file_path)
    if rewritten:
        log.info("reconcile: rewrote %d legacy slack trigger(s)", rewritten)
    return rewritten
