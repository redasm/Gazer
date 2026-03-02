"""Automatic Prompt Optimization (APO) — runtime prompt improvement engine.

Replaces the misuse of agent-lightning (RL training) with a lightweight
text-gradient approach that optimizes prompt templates using LLM
self-critique.  No GPU required.

This class is the core APO engine extracted from ``GazerEvolution``.
``GazerEvolution`` delegates to this class for the actual optimization
logic while maintaining its own scheduling and feedback collection.

References:
    - soul_architecture_reform.md Issue-07
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from soul.personality.personality_vector import PersonalityVector

logger = logging.getLogger("SoulAPO")


APO_META_PROMPT_TEMPLATE = """你是一个 Prompt 优化专家。根据以下信息，改进 AI 伴侣的系统提示词模板。

当前提示词模板：
{current_prompt}

用户喜欢的回复示例：
{liked_examples}

用户不喜欢的回复示例：
{disliked_examples}

AI 人格特征：
{personality}

请输出改进后的完整提示词模板。要求：
1. 保留原模板的核心结构和变量占位符
2. 强化用户喜欢的特征，弱化不喜欢的特征
3. 保持提示词简洁，不超过原长度的 1.5 倍
4. 直接输出改进后的模板文本，不要加任何解释
"""


class APOOptimizer:
    """Automatic Prompt Optimization — no GPU required.

    Analyzes feedback batches and uses LLM text-gradient to iteratively
    improve prompt templates.  Runs within the existing FastAPI service.
    """

    def __init__(
        self,
        llm_client: Any,
        min_feedback_count: int = 50,
    ) -> None:
        """
        Args:
            llm_client: Any object with an async ``call(prompt: str) -> str``
                method.
            min_feedback_count: Minimum number of feedback items required
                before optimization is attempted.
        """
        self._llm = llm_client
        self._min_feedback_count = min_feedback_count

    async def optimize_prompt(
        self,
        current_prompt: str,
        feedback_batch: list[dict[str, Any]],
        personality: "PersonalityVector | None" = None,
    ) -> str | None:
        """Attempt to produce an improved prompt template.

        Args:
            current_prompt: The current system prompt text.
            feedback_batch: List of feedback records.  Each must have at
                least ``"label"`` (``"positive"`` / ``"negative"``) and
                ``"content"`` keys.
            personality: Optional ``PersonalityVector`` for context.

        Returns:
            Improved prompt string, or ``None`` if feedback is insufficient.
        """
        if len(feedback_batch) < self._min_feedback_count:
            logger.info(
                "APO skipped: %d feedback items < minimum %d",
                len(feedback_batch),
                self._min_feedback_count,
            )
            return None

        liked = [
            fb.get("content", "")
            for fb in feedback_batch
            if fb.get("label") == "positive"
        ]
        disliked = [
            fb.get("content", "")
            for fb in feedback_batch
            if fb.get("label") == "negative"
        ]

        personality_info = personality.to_dict() if personality else {}

        meta_prompt = APO_META_PROMPT_TEMPLATE.format(
            current_prompt=current_prompt,
            liked_examples=liked[:20],  # cap to avoid token overflow
            disliked_examples=disliked[:20],
            personality=personality_info,
        )

        try:
            improved_prompt: str = await self._llm.call(meta_prompt)  # type: ignore[attr-defined]
            if not improved_prompt or not improved_prompt.strip():
                logger.warning("APO returned empty prompt — keeping current")
                return None
            logger.info("APO generated improved prompt (%d chars)", len(improved_prompt))
            return improved_prompt.strip()
        except Exception as exc:
            logger.error("APO optimization failed: %s", exc)
            return None
