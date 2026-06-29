"""AWS Bedrock provider — Converse API.

One provider for every Bedrock model family (Claude, Nova, Llama, …) via the
unified Converse API. GovCloud is the same provider with a ``us-gov-*`` region:
boto3 derives the GovCloud partition endpoint from the region name; an optional
endpoint override covers FIPS / PrivateLink. Auth is boto3-native — static
access keys (+ optional session token), a Bedrock API key (bearer token), or —
with no creds — the default chain (instance/IAM role). Message/tool/stream
translation lives in
``_converse.py``; this module owns the SDK client and error mapping.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any, Iterator

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.llm.errors import LLMError
from app.llm.providers._common import PREFLIGHT_TIMEOUT_SECONDS, run_preflight
from app.llm.providers._converse import build_converse_request, iter_stream_events
from app.llm.settings import LLMSettings

log = logging.getLogger(__name__)

StreamEvent = dict[str, Any]

_PROVIDER_LABEL = "Amazon Bedrock"


def _make_client(service: str, **kwargs: Any) -> Any:
    """``boto3.client`` behind an ``Any`` boundary — botocore is untyped, so the
    unavoidable Unknown is confined to this one seam."""
    return boto3.client(service, **kwargs)  # pyright: ignore


def _apply_bedrock_auth_env(bearer_token: str) -> None:
    """A Bedrock API key (bearer token) reaches botocore only through the
    AWS_BEARER_TOKEN_BEDROCK env var. Settings are a singleton, so the value is
    stable; set it when configured and clear it otherwise, so a later switch to
    access-key / IAM auth is not shadowed by a stale token."""
    if bearer_token:
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = bearer_token
    else:
        os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)


@lru_cache(maxsize=4)
def _client(
    access_key: str,
    secret_key: str,
    session_token: str,
    region: str,
    endpoint_url: str,
) -> Any:
    """Cached ``bedrock-runtime`` client. Empty credentials fall through to the
    boto3 default chain (instance/IAM role); an empty region/endpoint lets boto3
    derive the partition endpoint from the region name."""
    return _make_client(
        "bedrock-runtime",
        region_name=region or None,
        aws_access_key_id=access_key or None,
        aws_secret_access_key=secret_key or None,
        aws_session_token=session_token or None,
        endpoint_url=endpoint_url or None,
    )


class BedrockProvider:
    name = "bedrock"

    def check_configured(self, settings: LLMSettings) -> None:
        if not settings.bedrock_aws_region:
            raise LLMError(
                "not_configured",
                "AWS Bedrock region is not set. An admin needs to add it on the admin page.",
            )

    def test_connection(self, settings: LLMSettings, *, model: str) -> dict[str, Any]:
        """Preflight the saved config: a non-fatal model-listing probe (control
        plane) plus a decisive 1-token ``converse`` against ``model``."""
        _apply_bedrock_auth_env(settings.bedrock_aws_bearer_token)
        region = settings.bedrock_aws_region
        cfg: Any = BotoConfig(
            connect_timeout=PREFLIGHT_TIMEOUT_SECONDS,
            read_timeout=PREFLIGHT_TIMEOUT_SECONDS,
            retries={"max_attempts": 0},
        )
        creds: dict[str, Any] = {
            "region_name": region or None,
            "aws_access_key_id": settings.bedrock_aws_access_key_id or None,
            "aws_secret_access_key": settings.bedrock_aws_secret_access_key or None,
            "aws_session_token": settings.bedrock_aws_session_token or None,
            "config": cfg,
        }
        runtime: Any = _make_client(
            "bedrock-runtime", endpoint_url=settings.bedrock_endpoint_url or None, **creds
        )
        # Control plane has its own endpoint — don't borrow the runtime override.
        control: Any = _make_client("bedrock", **creds)
        display_endpoint = settings.bedrock_endpoint_url or (
            f"https://bedrock-runtime.{region}.amazonaws.com" if region else ""
        )
        return run_preflight(
            base_url=display_endpoint,
            auth_present=bool(
                settings.bedrock_aws_access_key_id or settings.bedrock_aws_bearer_token
            ),
            model=model,
            listing=lambda: control.list_foundation_models(),
            completion=lambda: runtime.converse(
                modelId=model,
                messages=[{"role": "user", "content": [{"text": "ping"}]}],
                inferenceConfig={"maxTokens": 1},
            ),
            translate=_translate_bedrock_error,
        )

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        settings: LLMSettings,
    ) -> Iterator[StreamEvent]:
        request = build_converse_request(messages, model=model, tools=tools, max_tokens=max_tokens)
        log.info(
            "llm request provider=bedrock model=%s tools=%d max_tokens=%d msgs=%d",
            model,
            len(tools or []),
            max_tokens,
            len(request["messages"]),
        )
        _apply_bedrock_auth_env(settings.bedrock_aws_bearer_token)
        client = _client(
            settings.bedrock_aws_access_key_id,
            settings.bedrock_aws_secret_access_key,
            settings.bedrock_aws_session_token,
            settings.bedrock_aws_region,
            settings.bedrock_endpoint_url,
        )
        try:
            response = client.converse_stream(**request)
            # Mid-stream service faults surface as botocore exceptions while
            # iterating response["stream"], so the loop is inside the try.
            for event in iter_stream_events(response["stream"]):
                if event["type"] == "done":
                    usage = event["usage"]
                    log.info(
                        "llm done provider=bedrock model=%s stop=%s tokens=%d/%d cached=%d",
                        model,
                        event["stop_reason"],
                        usage["input_tokens"],
                        usage["output_tokens"],
                        usage["cached_input_tokens"],
                    )
                yield event
        except LLMError:
            raise
        except Exception as exc:
            log.exception("llm provider error provider=bedrock model=%s", model)
            raise _translate_bedrock_error(exc) from exc


# botocore error code -> normalized LLMError code. Codes are shared by converse
# and converse_stream (the latter wraps mid-stream faults in EventStreamError,
# a ClientError subclass, so this handles both).
_ERROR_CODES: dict[str, tuple[str, str]] = {
    "AccessDeniedException": ("auth", "AWS denied access — check the IAM permissions / keys."),
    "UnrecognizedClientException": (
        "auth",
        "AWS rejected the credentials. An admin needs to update them.",
    ),
    "ThrottlingException": ("rate_limit", "Bedrock rate limit hit. Please retry in a moment."),
    "ResourceNotFoundException": (
        "config",
        "Bedrock could not find that model — check the model ID and region.",
    ),
    "ModelNotReadyException": (
        "config",
        "The Bedrock model is not ready yet. Please retry shortly.",
    ),
    "ValidationException": ("bad_request", "Bedrock rejected the request."),
    "ModelTimeoutException": ("provider", "The Bedrock model timed out. Please retry."),
    "ServiceUnavailableException": (
        "provider",
        "Bedrock is temporarily unavailable. Please retry.",
    ),
    "InternalServerException": ("provider", "Bedrock returned an internal error. Please retry."),
}


def _translate_bedrock_error(exc: Exception) -> LLMError:
    """Map a botocore exception to an LLMError. Detail-light — never echoes
    credentials, which can ride in the request that failed."""
    if isinstance(exc, ClientError):
        response: Any = getattr(exc, "response", None)
        error: Any = response.get("Error") if response else None
        raw_code: Any = error.get("Code") if error else None
        code = str(raw_code) if raw_code else ""
        mapped = _ERROR_CODES.get(code)
        if mapped is not None:
            return LLMError(mapped[0], mapped[1])
        return LLMError("provider", f"Bedrock error ({code or 'unknown'}).")
    if isinstance(exc, BotoCoreError):
        # Endpoint/region/connection problems before a response — e.g. a bad
        # region or unreachable (FIPS) endpoint.
        return LLMError(
            "network",
            "Could not reach Bedrock — check the region, endpoint, and network access.",
        )
    return LLMError("unknown", "Unexpected error talking to Bedrock.")


PROVIDER = BedrockProvider()


from app.llm.providers import register  # noqa: E402

register(PROVIDER)  # pyright: ignore[reportUnknownMemberType]
