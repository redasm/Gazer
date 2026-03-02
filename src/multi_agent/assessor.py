"""TaskComplexityAssessor — structured four-dimension scoring to decide
whether a user goal warrants multi-agent execution.

Dimensions (each 0 or 1, total score 0-4):
    1. Parallelizability   — can be split into 3+ independent subtasks
    2. Information breadth  — needs information from multiple sources
    3. High value           — user can tolerate >30 s for better results
    4. Low dependency       — subtasks rarely share intermediate results

Decision:
    score < 2  → single agent
    score >= 2 → multi-agent, with worker_hint derived from WORKER_COUNT_MAP
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from multi_agent.brain_router import HINT_FAST, BrainHint, DualBrainRouter

logger = logging.getLogger("multi_agent.Assessor")


@dataclass
class AssessmentResult:
    use_multi_agent: bool
    score: int            # 0-4
    worker_hint: int      # suggested worker cap for this task
    reason: str


ASSESSMENT_PROMPT = """\
判断以下任务是否适合多 Agent 并行执行。

任务：{goal}

回答以下 4 个问题（是=1分，否=0分）：
1. 任务可以被拆分成 3 个以上相互独立的子任务吗？
2. 完成任务需要从多个不同来源搜集和汇总信息吗？
3. 用户可以接受等待 30 秒以上来换取更好的结果吗？
4. 子任务之间的依赖关系少（不需要频繁共享中间结果）吗？

只输出 JSON，不要其他内容：
{{"score": <0-4>, "reason": "<一句话说明>"}}"""

WORKER_COUNT_MAP: dict[int, int] = {
    0: 0,
    1: 0,
    2: 2,
    3: 4,
    4: -1,  # -1 → use the user-configured max_workers limit
}

_FALLBACK = AssessmentResult(
    use_multi_agent=False,
    score=0,
    worker_hint=1,
    reason="评估失败，降级到单 Agent",
)


class TaskComplexityAssessor:
    """Fast-brain assessor that scores task complexity across 4 dimensions."""

    def __init__(
        self,
        router: DualBrainRouter,
        max_workers_limit: int = 5,
    ) -> None:
        self._router = router
        self._max_workers_limit = max_workers_limit

    async def assess(self, goal: str) -> AssessmentResult:
        prompt = ASSESSMENT_PROMPT.format(goal=goal[:800])

        try:
            raw = await self._router.generate(
                prompt=prompt,
                hint=HINT_FAST,
                temperature=0.0,
                max_tokens=128,
            )
        except Exception:
            logger.debug("Assessment LLM call failed", exc_info=True)
            return _FALLBACK

        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start < 0 or end <= start:
                raise ValueError("no JSON object found")
            data = json.loads(raw[start:end])
            score = int(data.get("score", 0))
            score = max(0, min(4, score))
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.debug("Assessment JSON parse failed: %s", raw[:200])
            return _FALLBACK

        use_multi = score >= 2
        worker_raw = WORKER_COUNT_MAP.get(score, 0)
        worker_hint = (
            self._max_workers_limit if worker_raw == -1
            else max(1, worker_raw)
        )

        return AssessmentResult(
            use_multi_agent=use_multi,
            score=score,
            worker_hint=worker_hint,
            reason=str(data.get("reason", "")),
        )
