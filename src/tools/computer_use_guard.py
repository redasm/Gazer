"""Computer use safety guard with emotion awareness.

Validates tool executions (like ADB, shell commands) with simple pattern
matching for dangerous actions. Additionally, if the AI is detected to be
agitated (high arousal, negative valence), the risk score is artificially
increased to prevent reckless execution.

References:
    - soul_architecture_reform_patch_v1.2.md Appendix-01
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from soul.affect.affective_state import AffectiveState


@dataclass
class RiskAssessment:
    """The result of a computer use safety check."""

    score: float  # 0.0 ~ 1.0
    requires_confirmation: bool
    reason: str


class ComputerUseGuard:
    """Safety guard for computer use actions, aware of emotional state."""

    DANGEROUS_PATTERNS = [
        (r"delete|remove|uninstall|清除|删除|卸载", 0.6),
        (r"send|post|submit|发送|提交|发帖", 0.5),
        (r"payment|transfer|pay|支付|转账|付款", 0.9),
        (r"password|token|credential|密码|凭证", 0.8),
        (r"format|wipe|reset|格式化|清空|重置", 0.9),
    ]

    def assess(
        self,
        instruction: str,
        current_affect: "AffectiveState | None" = None,
    ) -> RiskAssessment:
        """Assess the risk of an instruction, influenced by current affect.

        Args:
            instruction: The natural language or shell command to execute.
            current_affect: Optional current emotional state of the agent.

        Returns:
            RiskAssessment with score and confirmation requirement.
        """
        base_score = 0.0
        reason = ""

        for pattern, score in self.DANGEROUS_PATTERNS:
            if re.search(pattern, instruction, re.IGNORECASE):
                if score > base_score:
                    base_score = score
                    reason = f"指令包含高风险操作：{pattern}"

        # Emotion-driven risk boost
        affect_boost = 0.0
        affect_reason = ""
        if current_affect is not None:
            is_agitated = (
                current_affect.arousal > 0.6 and current_affect.valence < -0.3
            )
            if is_agitated:
                affect_boost = 0.25
                affect_reason = "检测到情绪激动状态，建议冷静后再执行"

        final_score = min(1.0, base_score + affect_boost)

        reason_parts = [r for r in (reason, affect_reason) if r]
        full_reason = " | ".join(reason_parts)

        return RiskAssessment(
            score=final_score,
            requires_confirmation=final_score >= 0.5,
            reason=full_reason or "操作安全",
        )
