"""Translation glue for the AWS Bedrock Converse API.

Pure dict<->dict translation between the normalized provider shape (see
``app.llm.client``) and Bedrock's Converse wire format — no ``boto3`` import,
so the request builder and the stream decoder are unit-testable without AWS.
``bedrock.py`` owns the SDK client and feeds ``iter_stream_events`` the raw
``converse_stream`` event iterator.
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator

from app.llm.providers._common import safe_json_loads, split_system, stringify_tool_result

StreamEvent = dict[str, Any]


def build_converse_request(
    messages: list[dict[str, Any]],
    *,
    model: str,
    tools: list[dict[str, Any]] | None,
    max_tokens: int,
) -> dict[str, Any]:
    """Normalized messages/tools -> kwargs for ``client.converse_stream``.

    Converse takes system text as a top-level ``system`` list (split out here)
    and every message ``content`` as a list of typed blocks, so a plain string
    turn becomes a single ``{"text": ...}`` block."""
    system_text, convo = split_system(messages)
    request: dict[str, Any] = {
        "modelId": model,
        "messages": [_converse_message(m) for m in convo],
        "inferenceConfig": {"maxTokens": max_tokens},
    }
    if system_text is not None:
        request["system"] = [{"text": system_text}]
    if tools:
        request["toolConfig"] = {"tools": [_tool_spec(t) for t in tools]}
    return request


def iter_stream_events(stream: Iterable[dict[str, Any]]) -> Iterator[StreamEvent]:
    """Decode a Converse ``converse_stream`` event iterator into our StreamEvents.

    Each event is a dict keyed by one wrapper name. Tool-use arguments arrive as
    ordered partial-JSON string fragments under ``contentBlockDelta`` and are
    concatenated per content-block index, then parsed once the block stops.
    Exactly one terminal ``done`` carries the stop reason + usage."""
    tool_blocks: dict[int, dict[str, Any]] = {}
    stop_reason = ""
    usage: dict[str, Any] = {}

    for event in stream:
        if "contentBlockStart" in event:
            block = event["contentBlockStart"]
            start: Any = block.get("start")
            tool_use: Any = start.get("toolUse") if start else None
            if tool_use is not None:
                tool_blocks[block["contentBlockIndex"]] = {
                    "id": tool_use["toolUseId"],
                    "name": tool_use["name"],
                    "buf": "",
                }
        elif "contentBlockDelta" in event:
            block = event["contentBlockDelta"]
            delta = block["delta"]
            if "text" in delta:
                yield {"type": "text_delta", "text": delta["text"]}
            elif "toolUse" in delta:
                rec = tool_blocks.get(block["contentBlockIndex"])
                if rec is not None:
                    rec["buf"] += delta["toolUse"].get("input", "")
        elif "contentBlockStop" in event:
            rec = tool_blocks.pop(event["contentBlockStop"]["contentBlockIndex"], None)
            if rec is not None:
                yield {
                    "type": "tool_call",
                    "id": rec["id"],
                    "name": rec["name"],
                    "arguments": safe_json_loads(rec["buf"]),
                }
        elif "messageStop" in event:
            stop_reason = event["messageStop"].get("stopReason", "") or ""
        elif "metadata" in event:
            usage = event["metadata"].get("usage") or {}

    in_tok = int(usage.get("inputTokens", 0) or 0)
    out_tok = int(usage.get("outputTokens", 0) or 0)
    # Converse reports inputTokens EXCLUDING cache read/write (like Anthropic),
    # so uncached = fresh input + cache writes, cached = cache reads.
    cache_read = int(usage.get("cacheReadInputTokens", 0) or 0)
    cache_write = int(usage.get("cacheWriteInputTokens", 0) or 0)
    yield {
        "type": "done",
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "reasoning_tokens": 0,
            "cached_input_tokens": cache_read,
            "uncached_input_tokens": in_tok + cache_write,
        },
    }


def _converse_message(m: dict[str, Any]) -> dict[str, Any]:
    """One normalized message -> a Converse ``{role, content: [...]}`` turn."""
    role = m["role"]
    if role == "tool":
        # Normalized tool result -> toolResult block carried on a user turn.
        return {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": m["tool_call_id"],
                        "content": [{"text": stringify_tool_result(m["content"])}],
                    }
                }
            ],
        }
    if role == "assistant" and m.get("tool_calls"):
        blocks: list[dict[str, Any]] = []
        if m.get("content"):
            blocks.append({"text": m["content"]})
        for tc in m["tool_calls"]:
            blocks.append(
                {"toolUse": {"toolUseId": tc["id"], "name": tc["name"], "input": tc["arguments"]}}
            )
        return {"role": "assistant", "content": blocks}

    return {"role": role, "content": [{"text": m["content"]}]}


def _tool_spec(tool: dict[str, Any]) -> dict[str, Any]:
    """Normalized tool spec -> Converse ``toolSpec`` (JSON Schema under inputSchema.json)."""
    spec: dict[str, Any] = {
        "name": tool["name"],
        "inputSchema": {"json": tool["input_schema"]},
    }
    if tool.get("description"):
        spec["description"] = tool["description"]
    return {"toolSpec": spec}
