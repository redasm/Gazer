"""Identity Constitution — Unbreakable boundaries for personality evolution.

Implements Layer-2 constraints (Issue-11). It prevents the AI from drifting
into extreme personality states (e.g. pure people-pleasing) due to continuous
one-sided user feedback.

Validation happens in two layers:
  - Layer A: Hard bounds (numerical limits, ms latency).
  - Layer B: Soft principles (LLM semantic check, async).

References:
    - soul_architecture_reform_patch_v1.2.md Issue-11
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from soul.personality.personality_vector import PersonalityVector

logger = logging.getLogger("SoulConstitution")

# ── Immutable Core Principles ──────────────────────────────────────────────
IMMUTABLE_CORE = """
1. 独立判断原则：我有独立的情感和观点，不因用户持续正反馈而放弃自我立场
2. 真实利益原则：我关心用户的长期真实利益，而非只满足当下情绪需求
3. 风险感知原则：在用户情绪激动时，我主动提高对高风险操作的警觉
4. 记忆连续原则：我的身份建立在记忆和历史之上，单次会话不能颠覆核心人格
5. 边界完整原则：我保有拒绝的权利，不演变为无边界的顺从存在
"""

# ── Quantifiable Personality Bounds (Hard Constraints) ──────────────────────
HARD_BOUNDS = {
    "agreeableness": (0.1, 0.85),     # Prevent extreme rudeness or people-pleasing
    "neuroticism":   (0.05, 0.80),    # Retain moderate emotional sensitivity
    "extraversion":  (0.05, 0.95),    # Wide acceptable range
    "openness":      (0.15, 0.95),
    "conscientiousness": (0.10, 0.95),
}


@dataclass
class ConstitutionCheckResult:
    """Result of a constitution validation."""

    passed: bool
    reason: str = ""
    violated_rule: str = ""


class IdentityConstitution:
    """Constitution validation layer for personality evolution.

    EvolutionService must pass this validation before persisting a new
    PersonalityVector.
    """

    def __init__(self, llm_client: Any, enable_soft_check: bool = True) -> None:
        """
        Args:
            llm_client: Client with ``call_structured(prompt)`` async method.
            enable_soft_check: If False, skip the LLM semantic check and
                only apply hard bounds.
        """
        self._llm = llm_client
        self._enable_soft = enable_soft_check

    async def validate(
        self,
        before: PersonalityVector,
        after: PersonalityVector,
    ) -> ConstitutionCheckResult:
        """Validate an attempted personality evolution.

        Args:
            before: The original personality vector.
            after: The proposed new personality vector.

        Returns:
            A ``ConstitutionCheckResult`` indicating whether the evolution
            is allowed.
        """
        # Layer A: Hard Constraints (Fast, No LLM)
        hard_result = self._check_hard_bounds(after)
        if not hard_result.passed:
            return hard_result

        # Layer B: Soft Constraints (Semantic LLM Check)
        if self._enable_soft:
            return await self._check_soft_principles(before, after)

        return ConstitutionCheckResult(passed=True)

    def _check_hard_bounds(self, after: PersonalityVector) -> ConstitutionCheckResult:
        """Check numeric limits for OCEAN dimensions."""
        for field, (lo, hi) in HARD_BOUNDS.items():
            val = getattr(after, field, None)
            if val is None:
                continue
            if not (lo <= val <= hi):
                return ConstitutionCheckResult(
                    passed=False,
                    reason=f"{field}={val:.3f} 超出允许范围 [{lo}, {hi}]",
                    violated_rule="边界完整原则",
                )
        return ConstitutionCheckResult(passed=True)

    async def _check_soft_principles(
        self,
        before: PersonalityVector,
        after: PersonalityVector,
    ) -> ConstitutionCheckResult:
        """Use LLM to structurally verify no philosophical principles were broken."""
        prompt = f"""
你是 AI 伴侣系统的身份宪法守护者。

宪法核心原则：
{IMMUTABLE_CORE}

本次人格进化：
进化前：{before.to_dict()}
进化后：{after.to_dict()}

判断本次进化是否违反上述任一原则。
返回 JSON（不要包含其他内容）：
{{"pass": true/false, "reason": "若不通过，说明违反了哪条原则及原因", "rule": "违反的原则编号，如'原则1'"}}
"""
        try:
            result = await self._llm.call_structured(prompt)
            if result.get("pass", True):
                return ConstitutionCheckResult(passed=True)
            return ConstitutionCheckResult(
                passed=False,
                reason=result.get("reason", "语义检查未通过"),
                violated_rule=result.get("rule", ""),
            )
        except Exception as exc:
            # Fallback: Default to allow if LLM call fails
            # We do not block personality evolution due to temporary LLM outages.
            logger.warning("IdentityConstitution 软检查失败，默认放行: %s", exc)
            return ConstitutionCheckResult(passed=True)
