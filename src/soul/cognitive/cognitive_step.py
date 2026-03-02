"""CognitiveStep — LLM call isolation abstraction layer.

State handlers call ``CognitiveStep.execute(context)`` instead of making
direct LLM API calls.  This allows:
  - Unit-testing state transitions with ``MockCognitiveStep``
  - Swapping LLM backends without touching business logic
  - Clean separation of concerns (state machine ≠ LLM client)

References:
    - soul_architecture_reform.md Issue-03, Issue-09
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Generic, TypeVar

from soul.memory.working_context import WorkingContext

if TYPE_CHECKING:
    from soul.cognitive.context_budget_manager import ContextBudgetManager
    from soul.personality.personality_vector import PersonalityVector

logger = logging.getLogger("SoulCognitive")

T = TypeVar("T")


class CognitiveStep(ABC, Generic[T]):
    """Abstract cognitive step — one unit of LLM-mediated reasoning.

    Implementations encapsulate all LLM interaction (prompt building, API
    call, response parsing) so that the state machine only sees a clean
    ``execute`` interface.
    """

    @abstractmethod
    async def execute(self, context: WorkingContext) -> tuple[T, WorkingContext]:
        """Run one cognitive step.

        Args:
            context: Immutable ``WorkingContext`` snapshot.

        Returns:
            A tuple of ``(typed_result, new_context)``.
            The input ``context`` must NOT be modified — use
            ``context.with_update(...)`` to produce a new snapshot.
        """
        ...


class ReflectStep(CognitiveStep[str]):
    """Concrete step that sends a reflection prompt to the LLM.

    Uses ``ContextBudgetManager.build_prompt()`` to assemble the prompt
    from ``WorkingContext`` slots with proper token budgeting and
    Lost-in-the-Middle layout (Issue-09).
    """

    def __init__(
        self,
        llm_client: object,
        budget_manager: "ContextBudgetManager",
        personality: "PersonalityVector | None" = None,
    ) -> None:
        """
        Args:
            llm_client: Any object with an async ``call(prompt: str) -> str``
                method (duck-typed for flexibility).
            budget_manager: Manages token budgets and prompt assembly.
            personality: Optional ``PersonalityVector`` for prompt rendering.
        """
        self._llm = llm_client
        self._budget_manager = budget_manager
        self._personality = personality
        # Mutable history buffer — caller may append turns between executions.
        self.history: list[dict[str, str]] = []

    async def execute(self, context: WorkingContext) -> tuple[str, WorkingContext]:
        prompt = self._budget_manager.build_prompt(
            context,
            personality=self._personality,
            history=self.history or None,
        )
        response: str = await self._llm.call(prompt)  # type: ignore[attr-defined]
        new_ctx = context.with_update(turn_count=context.turn_count + 1)
        return response, new_ctx


class MockCognitiveStep(CognitiveStep[str]):
    """Deterministic mock for unit testing — no LLM dependency.

    Returns a pre-configured response string.
    """

    def __init__(self, response: str = "mock_response") -> None:
        self._response = response
        self.call_count: int = 0
        self.last_context: WorkingContext | None = None

    async def execute(self, context: WorkingContext) -> tuple[str, WorkingContext]:
        self.call_count += 1
        self.last_context = context
        new_ctx = context.with_update(turn_count=context.turn_count + 1)
        return self._response, new_ctx
