"""Handler for the `upload_image` tool. Spec lives in `upload_image.json`.

Ingest goes through `app/wiki/image_upload.py`, so an agent upload is bound by
the same anchor, permission and format rules as any other.
"""

from __future__ import annotations

import binascii
import base64
from typing import Any

from app.auth import PermissionDenied, current_user
from app.llm.agents.tools.errors import ToolError
from app.wiki import image_upload


def handle(args: dict[str, Any]) -> Any:
    try:
        path = args.get("path")
        encoded = args.get("data_base64")
        alt_text = args.get("alt_text")
        if not isinstance(path, str) or not path.strip():
            raise ToolError("path is required")
        if not isinstance(encoded, str) or not encoded.strip():
            raise ToolError("data_base64 is required")

        user = current_user()
        if user is None:
            raise ToolError("no authenticated user to attribute the upload to")

        # Base64 costs 4 bytes per 3 encoded, so the cap is checkable before
        # the argument is expanded into bytes.
        if len(encoded) > (image_upload.UPLOAD_CAP_BYTES + 2) // 3 * 4:
            raise ToolError("image exceeds 10 MiB limit")

        try:
            # validate=True so a truncated or mangled payload is a clear error
            # rather than silently decoding to bytes that fail the sniff.
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ToolError(f"data_base64 is not valid base64: {exc}") from exc

        anchor = image_upload.validate_anchor(path)
        result = image_upload.store(
            data=data,
            anchor=anchor,
            filename=alt_text if isinstance(alt_text, str) else None,
            user=user,
        )
        return {
            "url": result.url,
            "markdown": result.markdown,
            "path": anchor.rel,
        }
    except PermissionDenied as exc:
        return {"error": str(exc)}
    except image_upload.ImageUploadError as exc:
        return {"error": exc.message}
    except ToolError as exc:
        return {"error": str(exc)}
