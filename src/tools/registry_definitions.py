from __future__ import annotations

from typing import Any


def list_tool_definitions(
    tools: dict[str, Any],
    *,
    is_allowed: Any,
    policy: Any = None,
    sender_id: str = "",
    channel: str = "",
    model_provider: str = "",
    model_name: str = "",
) -> list[dict[str, Any]]:
    """Return OpenAI-compatible tool schemas filtered by the access checker."""
    return [
        tool.to_schema()
        for tool in tools.values()
        if is_allowed(
            tool.name,
            policy=policy,
            sender_id=sender_id,
            channel=channel,
            model_provider=model_provider,
            model_name=model_name,
        )
    ]
