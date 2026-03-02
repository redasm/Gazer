"""Session distillation service — Layer-2 personality evolution.

After a conversation session ends, this service uses LLM analysis of the
session transcript and feedback events to derive personality adjustments.
Results are persisted to OpenViking via ``MemoryPort``.

Layer-2 sits between:
  - Layer-1 (immediate per-turn feedback in ``PersonalityVector.apply_feedback``)
  - Layer-3 (APO prompt optimization in ``apo_optimizer.py``)

References:
    - soul_architecture_reform.md Issue-06 (Layer 2)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from soul.personality.identity_constitution import IdentityConstitution
from soul.personality.personality_vector import PersonalityDelta, PersonalityVector

if TYPE_CHECKING:
    from soul.memory.memory_port import MemoryPort

logger = logging.getLogger("SoulEvolution")


DISTILL_PROMPT_TEMPLATE = """你是一个人格进化分析师。根据以下会话记录和用户反馈，推断 AI 伴侣应该做出的人格调整。

会话记录（摘要）：
{transcript}

用户喜欢的内容：
{liked}

用户不喜欢的内容：
{disliked}

当前人格向量：
{current}

请输出 JSON，包含以下字段（每个值为 -1.0 到 1.0 之间的调整幅度，0.0 表示不变）：
- openness
- conscientiousness
- extraversion
- agreeableness
- neuroticism
- humor_level
- verbosity
- formality
"""


@dataclass
class FeedbackEvent:
    """A single feedback event within a session."""

    positive: bool
    content: str
    timestamp: float = 0.0


class SessionDistiller:
    """Layer-2 personality evolution: post-session LLM-based distillation.

    Analyzes session transcripts and feedback events to produce a
    ``PersonalityDelta`` that is applied to the current personality vector.
    """

    def __init__(
        self,
        llm_client: Any,
        memory_port: "MemoryPort | None" = None,
        constitution: IdentityConstitution | None = None,
        user_id: str = "default",
    ) -> None:
        """
        Args:
            llm_client: Any object with ``call_structured(prompt, schema=...)``
                async method.
            memory_port: Optional ``MemoryPort`` for persisting personality
                history snapshots.
            constitution: Optional ``IdentityConstitution`` validation layer.
            user_id: User identifier for scoped storage.
        """
        self._llm = llm_client
        self._memory = memory_port
        self._constitution = constitution
        self._user_id = user_id

    @property
    def has_llm(self) -> bool:
        """Return True if an LLM client has been configured."""
        return self._llm is not None

    def set_llm_client(self, llm_client: Any) -> None:
        """Set or replace the LLM client used for distillation."""
        self._llm = llm_client

    async def distill_session(
        self,
        transcript: list[dict[str, Any]],
        feedback_events: list[FeedbackEvent],
        current_personality: PersonalityVector,
    ) -> PersonalityVector:
        """Analyze a completed session and return an updated personality.

        Args:
            transcript: List of dialogue turns ``{"user": ..., "assistant": ...}``.
            feedback_events: Positive/negative feedback collected during session.
            current_personality: The personality vector before distillation.

        Returns:
            A new ``PersonalityVector`` reflecting the session's insights.
        """
        liked = [e.content for e in feedback_events if e.positive]
        disliked = [e.content for e in feedback_events if not e.positive]

        prompt = DISTILL_PROMPT_TEMPLATE.format(
            transcript=transcript,
            liked=liked,
            disliked=disliked,
            current=current_personality.to_dict(),
        )

        try:
            delta_dict: dict[str, float] = await self._llm.call_structured(
                prompt, schema=dict
            )
            delta = PersonalityDelta(
                openness=float(delta_dict.get("openness", 0.0)),
                conscientiousness=float(delta_dict.get("conscientiousness", 0.0)),
                extraversion=float(delta_dict.get("extraversion", 0.0)),
                agreeableness=float(delta_dict.get("agreeableness", 0.0)),
                neuroticism=float(delta_dict.get("neuroticism", 0.0)),
                humor_level=float(delta_dict.get("humor_level", 0.0)),
                verbosity=float(delta_dict.get("verbosity", 0.0)),
                formality=float(delta_dict.get("formality", 0.0)),
            )
        except Exception as exc:
            logger.warning("Session distillation LLM call failed: %s", exc)
            return current_personality

        new_personality = current_personality.apply_delta(delta)

        # ── Constitution Validation (Issue-11) ─────────────────────────
        if self._constitution is not None:
            check = await self._constitution.validate(current_personality, new_personality)
            if not check.passed:
                logger.warning(
                    "人格进化被宪法拒绝 | 原因: %s | 违反: %s",
                    check.reason,
                    check.violated_rule,
                )
                if self._memory is not None:
                    try:
                        await self._memory.store(
                            key=f"constitution_reject:{int(time.time())}",
                            content={
                                "before": current_personality.to_dict(),
                                "rejected": new_personality.to_dict(),
                                "reason": check.reason,
                                "rule": check.violated_rule,
                            },
                        )
                    except Exception as exc:
                        logger.warning("Failed to persist constitution reject: %s", exc)
                return current_personality  # Return original, do not write rejected state

        # Persist personality history to OpenViking
        if self._memory is not None:
            try:
                await self._memory.store(
                    key=f"personality:user:{self._user_id}:history:{int(time.time())}",
                    content={
                        "before": current_personality.to_dict(),
                        "after": new_personality.to_dict(),
                        "trigger": "session_distillation",
                    },
                )
            except Exception as exc:
                logger.warning("Failed to persist personality history: %s", exc)

        return new_personality
