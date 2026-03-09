"""AgentLoop mixin: Planning.

Extracted from loop.py to reduce file size.
Contains 3 methods.
"""

from __future__ import annotations

import copy
from agent.constants import *  # noqa: F403
import re
from runtime.resilience import RetryBudget
import logging
logger = logging.getLogger('AgentLoop')

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # Add type imports as needed


# Internal-only single-agent planning policy.
# If you want to tune planning behavior, edit this constant directly.
INTERNAL_PLANNING_POLICY: Dict[str, Any] = {
    "mode": "auto",  # always | auto | off
    "auto": {
        "min_message_chars": 220,
        "min_history_messages": 8,
        "min_line_breaks": 2,
        "min_list_lines": 2,
    },
}


class PlanningMixin:
    """Mixin providing planning functionality."""

    @staticmethod
    def _planning_policy() -> Dict[str, Any]:
        raw = copy.deepcopy(INTERNAL_PLANNING_POLICY)
        if not isinstance(raw, dict):
            raw = {}
        mode = str(raw.get("mode", "auto") or "auto").strip().lower()
        if mode not in {"always", "auto", "off"}:
            mode = "auto"
        auto = raw.get("auto", {}) or {}
        if not isinstance(auto, dict):
            auto = {}

        def _read_int(name: str, default: int, minimum: int = 0) -> int:
            try:
                parsed = int(auto.get(name, default))
            except (TypeError, ValueError):
                parsed = default
            return parsed if parsed >= minimum else default

        return {
            "mode": mode,
            "min_message_chars": _read_int("min_message_chars", 220, minimum=1),
            "min_history_messages": _read_int("min_history_messages", 8, minimum=0),
            "min_line_breaks": _read_int("min_line_breaks", 2, minimum=0),
            "min_list_lines": _read_int("min_list_lines", 2, minimum=0),
        }

    @classmethod
    def _should_plan(cls, message: str, *, history_len: int = 0) -> bool:
        """Policy-driven planning gate (no keyword matching)."""
        policy = cls._planning_policy()
        mode = str(policy.get("mode", "auto"))
        if mode == "off":
            return False
        if mode == "always":
            return True

        text = str(message or "").strip()
        if not text:
            return False
        line_breaks = text.count("\n")
        list_lines = sum(
            1
            for line in text.splitlines()
            if re.match(r"^\s*(?:[-*]\s+|\d+\.\s+)", str(line or ""))
        )
        return (
            len(text) >= int(policy.get("min_message_chars", 220))
            or int(history_len) >= int(policy.get("min_history_messages", 8))
            or int(line_breaks) >= int(policy.get("min_line_breaks", 2))
            or int(list_lines) >= int(policy.get("min_list_lines", 2))
        )

    async def _generate_plan(
        self,
        messages: List[Dict[str, Any]],
        *,
        retry_budget: RetryBudget,
    ) -> Optional[str]:
        """Ask the LLM to produce a plan before executing."""
        plan_prompt = {
            "role": "system",
            "content": (
                "Before executing, create a brief numbered plan of steps you will take. "
                "Keep it concise (max 5 steps). Output only the plan."
            ),
        }
        plan_messages = messages + [plan_prompt]
        try:
            response = await self._call_llm_with_retries(
                messages=plan_messages,
                tools=[],  # No tool calls during planning
                model=self.model,
                call_name="Plan LLM call",
                retry_budget=retry_budget,
            )
            if response.request_id:
                logger.info(
                    "Plan LLM call: model=%s request_id=%s tokens=%s",
                    response.model, response.request_id,
                    response.usage.get("total_tokens", "?"),
                    extra={
                        "request_id": response.request_id,
                        "model": response.model,
                        "tokens": response.usage,
                    },
                )
            if response.usage:
                self.usage.add(response.usage, model=response.model or "")
            return response.content
        except Exception as e:
            logger.warning("Plan generation failed: %s", e)
            return None

