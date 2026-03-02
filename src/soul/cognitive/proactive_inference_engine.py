"""Proactive inference engine — anticipating user needs before they ask.

Instead of only reacting to explicit user input, the engine analyzes
``WorkingContext`` and recent emotional trends to infer what the user
might need next, then injects hints into ``agent_context``.

Rules:
  - First 3 turns: no proactive inference (insufficient data)
  - Signals below confidence threshold: not injected

References:
    - soul_architecture_reform.md Issue-10 (v1.1)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from soul.affect.affective_state import AffectiveState
from soul.memory.working_context import WorkingContext

logger = logging.getLogger("SoulProactiveInference")


# ------------------------------------------------------------------
# Prompt template (maintained separately for APO optimization)
# ------------------------------------------------------------------

PROACTIVE_INFERENCE_PROMPT = """\
你是一个善于观察人类情绪的陪伴型 AI。根据以下信息，推断用户可能存在但尚未明确表达的需求。

近期对话内容：
{recent_inputs}

情绪趋势：{affect_trend}
当前情绪：{current_affect}
会话轮数：{session_turns}

可能的信号类型：
- emotional_support_needed: 情绪低落但未求助
- topic_avoidance: 反复回避某话题
- unfinished_thought: 话语未完，欲言又止
- repeated_concern: 同一担忧多次出现
- energy_drop: 活跃度明显下降

返回 JSON 数组（不要包含其他内容）：
[{{"type": "<signal_type>", "confidence": 0.0~1.0, "hint": "给 CognitiveStep 的提示"}}]
"""


class ProactiveSignalType(str, Enum):
    """Categories of proactive signals (Issue-10 spec)."""

    EMOTIONAL_SUPPORT_NEEDED = "emotional_support_needed"  # 情绪低落但未求助
    TOPIC_AVOIDANCE = "topic_avoidance"                    # 反复回避某话题
    UNFINISHED_THOUGHT = "unfinished_thought"              # 话语未完，欲言又止
    REPEATED_CONCERN = "repeated_concern"                  # 同一担忧多次出现
    ENERGY_DROP = "energy_drop"                            # 活跃度明显下降


@dataclass
class ProactiveSignal:
    """A single inferred user need.

    Attributes:
        signal_type: What kind of need was detected.
        confidence: How confident the engine is (0.0 ~ 1.0).
        suggested_response_hint: Hint for ``CognitiveStep`` prompt injection.
    """

    signal_type: ProactiveSignalType
    confidence: float
    suggested_response_hint: str


class ProactiveInferenceEngine:
    """Infers latent user needs from context and emotional trajectory.

    Call ``infer()`` before ``CognitiveStep.execute()`` to produce signals,
    then ``inject_hints()`` to append them to ``agent_context``.

    Uses a two-layer approach:
      1. Fast rule-based heuristics (always runs, no LLM cost)
      2. LLM-based inference (optional, for nuanced signals)
    """

    CONFIDENCE_THRESHOLD = 0.6

    def __init__(
        self,
        llm_client: object | None = None,
        confidence_threshold: float = 0.6,
        min_turns: int = 3,
    ) -> None:
        """
        Args:
            llm_client: Optional LLM client with ``call_structured`` method.
                When ``None``, only rule-based heuristics are used.
            confidence_threshold: Signals below this threshold are discarded.
            min_turns: No inference before this many turns have elapsed.
        """
        self._llm = llm_client
        self._confidence_threshold = confidence_threshold
        self._min_turns = min_turns

    async def infer(
        self,
        context: WorkingContext,
        affect_history: list[AffectiveState] | None = None,
        session_turn_count: int | None = None,
    ) -> list[ProactiveSignal]:
        """Analyze context and emotional trajectory for latent needs.

        Args:
            context: Current ``WorkingContext`` snapshot.
            affect_history: Recent affect states from
                ``AffectiveStateManager.get_history()``.
            session_turn_count: Explicit turn count override.  Falls back
                to ``context.turn_count`` when ``None``.

        Returns:
            List of ``ProactiveSignal`` instances (may be empty).
        """
        turn_count = (
            session_turn_count if session_turn_count is not None
            else context.turn_count
        )
        if turn_count < self._min_turns:
            return []

        # Layer 1: rule-based heuristics (fast, no LLM)
        signals = self._rule_based_infer(context, affect_history)

        # Layer 2: LLM-based inference (optional)
        if self._llm is not None:
            llm_signals = await self._llm_infer(
                context, affect_history, turn_count
            )
            signals.extend(llm_signals)

        # Deduplicate by signal type, keeping highest confidence
        seen: dict[ProactiveSignalType, ProactiveSignal] = {}
        for s in signals:
            if s.signal_type not in seen or s.confidence > seen[s.signal_type].confidence:
                seen[s.signal_type] = s

        return [
            s for s in seen.values()
            if s.confidence >= self._confidence_threshold
        ]

    def inject_hints(
        self,
        context: WorkingContext,
        signals: list[ProactiveSignal],
    ) -> WorkingContext:
        """Append proactive signals to ``agent_context``.

        Args:
            context: Current ``WorkingContext``.
            signals: Filtered proactive signals to inject.

        Returns:
            New ``WorkingContext`` with signals appended to ``agent_context``.
        """
        if not signals:
            return context

        hints = tuple(
            f"[主动推断:{s.signal_type.value}({s.confidence:.0%})] "
            f"{s.suggested_response_hint}"
            for s in signals
        )
        new_agent_ctx = context.agent_context + hints
        return context.with_update(agent_context=new_agent_ctx)

    # ------------------------------------------------------------------
    # Layer 1: Rule-based heuristics
    # ------------------------------------------------------------------

    def _rule_based_infer(
        self,
        context: WorkingContext,
        affect_history: list[AffectiveState] | None,
    ) -> list[ProactiveSignal]:
        signals: list[ProactiveSignal] = []

        # Emotional distress detection
        if context.affect.valence < -0.5:
            confidence = min(1.0, abs(context.affect.valence) * 0.8)
            signals.append(
                ProactiveSignal(
                    signal_type=ProactiveSignalType.EMOTIONAL_SUPPORT_NEEDED,
                    confidence=confidence,
                    suggested_response_hint=(
                        "用户情绪低落但未主动求助，建议温和地关心用户状态"
                    ),
                )
            )

        # Declining valence trend → emotional support needed
        if affect_history and len(affect_history) >= 3:
            recent_valences = [a.valence for a in affect_history[-3:]]
            is_val_declining = all(
                recent_valences[i] > recent_valences[i + 1]
                for i in range(len(recent_valences) - 1)
            )
            if is_val_declining and recent_valences[-1] < 0:
                signals.append(
                    ProactiveSignal(
                        signal_type=ProactiveSignalType.EMOTIONAL_SUPPORT_NEEDED,
                        confidence=0.7,
                        suggested_response_hint=(
                            "用户情绪持续下降趋势，主动关心用户近况"
                        ),
                    )
                )

        # Energy / arousal drop trend
        if affect_history and len(affect_history) >= 3:
            recent_arousals = [a.arousal for a in affect_history[-3:]]
            is_dropping = all(
                recent_arousals[i] > recent_arousals[i + 1]
                for i in range(len(recent_arousals) - 1)
            )
            if is_dropping and recent_arousals[-1] < -0.2:
                signals.append(
                    ProactiveSignal(
                        signal_type=ProactiveSignalType.ENERGY_DROP,
                        confidence=0.7,
                        suggested_response_hint=(
                            "用户活跃度明显下降，可能需要休息或转换话题"
                        ),
                    )
                )

        # Short / empty input → might be unfinished thought
        user_input = context.user_input.strip()
        if user_input and len(user_input) < 5 and context.turn_count > 5:
            signals.append(
                ProactiveSignal(
                    signal_type=ProactiveSignalType.UNFINISHED_THOUGHT,
                    confidence=0.5,
                    suggested_response_hint=(
                        "用户输入简短，可能欲言又止，鼓励用户继续表达"
                    ),
                )
            )

        return signals

    # ------------------------------------------------------------------
    # Layer 2: LLM-based inference
    # ------------------------------------------------------------------

    async def _llm_infer(
        self,
        context: WorkingContext,
        affect_history: list[AffectiveState] | None,
        session_turn_count: int,
    ) -> list[ProactiveSignal]:
        """Call LLM for nuanced signal detection."""
        prompt = PROACTIVE_INFERENCE_PROMPT.format(
            recent_inputs=self._extract_recent_inputs(context),
            affect_trend=self._summarize_affect_trend(affect_history),
            current_affect=context.affect.to_label(),
            session_turns=session_turn_count,
        )
        try:
            raw: list[dict] = await self._llm.call_structured(prompt)  # type: ignore[union-attr]
            return [
                ProactiveSignal(
                    signal_type=ProactiveSignalType(r["type"]),
                    confidence=float(r.get("confidence", 0.5)),
                    suggested_response_hint=r.get("hint", ""),
                )
                for r in raw
                if r.get("type") in {e.value for e in ProactiveSignalType}
            ]
        except Exception as exc:
            logger.warning("LLM proactive inference failed: %s", exc)
            return []

    @staticmethod
    def _summarize_affect_trend(
        history: list[AffectiveState] | None,
    ) -> str:
        if not history:
            return "无历史数据"
        avg_valence = sum(a.valence for a in history) / len(history)
        trend = "下降" if history[-1].valence < history[0].valence else "上升或平稳"
        return f"近 {len(history)} 轮情绪效价均值 {avg_valence:.2f}，趋势{trend}"

    @staticmethod
    def _extract_recent_inputs(context: WorkingContext) -> str:
        return (
            "\n".join(context.session_context[-5:])
            if context.session_context
            else ""
        )
