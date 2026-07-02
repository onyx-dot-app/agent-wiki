"""Reshapes legacy trigger YAML into the destination-config shape.

Files written before destination configs carried a single ``message`` /
``destination`` / ``slack_webhook_id`` at the top level. This rewrites them to
an ``actions`` list. A ``slack_webhook_id`` resolves through the destination
config that mirrored it (the ``from_slack_webhook`` marker written when the
webhook store migrated into the registry); when no mirror exists the trigger
falls back to event-log delivery with a loud warning naming the file and
owner.

Runs at boot before the cache rebuild. Already-reshaped triggers carry an
``actions`` list and are skipped, so it is a no-op after the first boot.
"""
from __future__ import annotations

import logging
from typing import Any, cast

import yaml

from app.triggers import destination_configs as dest_configs
from app.triggers import storage
from app.wiki import git as wiki_git

log = logging.getLogger(__name__)

# Marker in a mirrored config's config_json naming its source webhook.
_SOURCE_KEY = "from_slack_webhook"


def _find_mirror(webhook_id: str, owner_user_id: str) -> str | None:
    """The destination config that mirrored ``webhook_id`` for its owner, or
    None. Lookup only — mirrors were created when the webhook store migrated
    into the registry."""
    for cfg in dest_configs.list_for_user(owner_user_id):
        config = cfg.get("config")
        if isinstance(config, dict) and cast(dict[str, Any], config).get(_SOURCE_KEY) == webhook_id:
            return cast(str, cfg["id"])
    return None


def reconcile_legacy_slack_triggers() -> int:
    """Rewrite trigger YAML still on the legacy single-destination shape into
    the destination-config shape. Returns the number of files rewritten."""
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
            config_id = _find_mirror(webhook_id, owner)
            if config_id is None:
                # Name the drop so operators can find affected triggers.
                log.warning(
                    "reconcile: %s (owner %s) references slack webhook %s with no "
                    "mirrored destination config; trigger falls back to event-log only",
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
