"""Token budget manager — prompt assembly with Lost-in-the-Middle awareness.

Manages how ``WorkingContext`` slots are assembled into the final LLM prompt,
respecting token limits and placing information according to the
Lost-in-the-Middle effect (high-value content at the head and tail, lower-
value content in the middle).

Prompt layout:
  HEAD: personality + affect (agent_context)
  MIDDLE: user history → session memory → dialogue history
  TAIL: current user input

References:
    - soul_architecture_reform.md Issue-09 (v1.1)
    - Liu et al. (2023) "Lost in the Middle" — NeurIPS
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from soul.memory.working_context import WorkingContext

if TYPE_CHECKING:
    from soul.personality.personality_vector import PersonalityVector

logger = logging.getLogger("SoulContextBudget")

# CJK characters map to ~1–2 tokens; mixed Chinese-English averages ~3
# chars per token.  English-only would be ~4.
DEFAULT_CHARS_PER_TOKEN = 3.0


@dataclass
class SlotBudget:
    """Token budget allocation for each context slot.

    Field names mirror the ``WorkingContext`` slot names (Issue-09 v1.1).

    Attributes:
        agent_context: Budget for AI personality + affect description.
        user_context: Budget for user history and preferences.
        session_context: Budget for current session short-term memory.
        history: Budget for dialogue history turns.
        user_input: Budget for the current user input.
        total_max: Hard ceiling for the entire prompt.
    """

    agent_context: int = 300
    user_context: int = 400
    session_context: int = 600
    history: int = 600
    user_input: int = 300
    total_max: int = 2500


class ContextBudgetManager:
    """Assembles ``WorkingContext`` into a token-budgeted prompt string.

    Uses the Lost-in-the-Middle principle to place high-priority content
    at the head and tail of the prompt.
    """

    def __init__(
        self,
        budget: SlotBudget | None = None,
        chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
    ) -> None:
        self._budget = budget or SlotBudget()
        self._chars_per_token = chars_per_token

    def estimate_tokens(self, text: str) -> int:
        """Rough token count estimate from character count."""
        if not text:
            return 0
        return max(1, int(len(text) / self._chars_per_token))

    def build_prompt(
        self,
        context: WorkingContext,
        personality: "PersonalityVector | None" = None,
        history: list[dict[str, str]] | None = None,
        *,
        personality_prompt: str = "",
        extra_head: str = "",
        extra_tail: str = "",
    ) -> str:
        """Assemble the final prompt from context slots.

        Layout (Lost-in-the-Middle):
          1. HEAD: personality description + affect label + extra_head
          2. MIDDLE: user_context → session_context → history
          3. TAIL: user_input + extra_tail

        Each section is truncated to its token budget.

        Args:
            context: The ``WorkingContext`` snapshot to assemble.
            personality: Optional ``PersonalityVector``; ``to_prompt()``
                is used when *personality_prompt* is not given.
            history: Optional list of dialogue turns, each a dict with
                ``"user"`` and ``"assistant"`` keys.
            personality_prompt: Pre-built personality description string.
                If given, takes precedence over *personality*.
            extra_head: Additional text to prepend (e.g. system instructions).
            extra_tail: Additional text to append (e.g. response format hints).

        Returns:
            The assembled prompt string.
        """
        sections: list[str] = []

        # ── HEAD: personality + affect ─────────────────────────────
        head_parts: list[str] = []
        if extra_head:
            head_parts.append(extra_head)
        persona_text = personality_prompt or (
            personality.to_prompt() if personality else ""
        )
        if persona_text:
            head_parts.append(persona_text)
        head_parts.append(
            f"当前情绪：{context.affect.to_label()} "
            f"(valence={context.affect.valence:.2f}, "
            f"arousal={context.affect.arousal:.2f})"
        )
        for item in context.agent_context:
            head_parts.append(item)
        head_text = self._truncate(
            "\n".join(head_parts), self._budget.agent_context
        )
        sections.append(head_text)

        # ── MIDDLE: user history → session memory → dialogue history ──
        if context.user_context:
            user_text = self._render_slot(
                "用户背景", context.user_context, self._budget.user_context
            )
            if user_text:
                sections.append(user_text)

        if context.session_context:
            session_text = self._render_slot(
                "近期记忆", context.session_context, self._budget.session_context
            )
            if session_text:
                sections.append(session_text)

        if history:
            history_text = self._render_history(history)
            if history_text:
                sections.append(history_text)

        # ── TAIL: current user input ───────────────────────────
        tail_parts: list[str] = []
        if context.user_input:
            tail_parts.append(f"用户：{context.user_input}")
        if extra_tail:
            tail_parts.append(extra_tail)
        if tail_parts:
            tail_text = self._truncate(
                "\n".join(tail_parts), self._budget.user_input
            )
            sections.append(tail_text)

        full_prompt = "\n\n".join(sections)

        # ── Enforce total ceiling ──────────────────────────────
        total_tokens = self.estimate_tokens(full_prompt)

        logger.info(
            "ContextBudgetManager Tokens usage - Est. Total: %d, Max: %d",
            total_tokens,
            self._budget.total_max,
        )

        if total_tokens > self._budget.total_max:
            max_chars = int(self._budget.total_max * self._chars_per_token)
            # Preserve head (personality/affect) and tail (user input);
            # truncate middle sections first.
            head_chars = min(len(sections[0]), max_chars // 3) if sections else 0
            tail_chars = min(len(sections[-1]), max_chars // 3) if len(sections) > 1 else 0
            middle_budget = max(0, max_chars - head_chars - tail_chars)
            head = full_prompt[:head_chars]
            tail = full_prompt[-tail_chars:] if tail_chars else ""
            middle = full_prompt[head_chars:len(full_prompt) - tail_chars if tail_chars else len(full_prompt)]
            if len(middle) > middle_budget:
                middle = middle[:middle_budget] + "\n[...截断...]"
            full_prompt = head + middle + tail
            logger.warning(
                "Prompt truncated: %d tokens > %d max (middle-first strategy)",
                total_tokens,
                self._budget.total_max,
            )

        return full_prompt

    # ------------------------------------------------------------------
    # Slot rendering helpers
    # ------------------------------------------------------------------

    def _render_slot(
        self, label: str, items: tuple[str, ...], budget: int
    ) -> str:
        """Render a context slot with label, truncated per item to budget."""
        if not items:
            return ""
        result: list[str] = []
        used = 0
        for item in items:
            cost = self.estimate_tokens(item)
            if used + cost > budget:
                break
            result.append(item)
            used += cost
        if not result:
            return ""
        return f"[{label}]\n" + "\n".join(result)

    def _render_history(self, history: list[dict[str, str]]) -> str:
        """Render dialogue history turns, keeping the most recent first.

        Keeps turns from the end of the list (most recent) and works
        backwards until the history token budget is exhausted.
        """
        budget = self._budget.history
        result: list[str] = []
        used = 0
        for turn in reversed(history):
            user_text = turn.get("user", "")
            assistant_text = turn.get("assistant", "")
            text = f"用户：{user_text}\nAI：{assistant_text}"
            cost = self.estimate_tokens(text)
            if used + cost > budget:
                break
            result.insert(0, text)  # maintain chronological order
            used += cost
        return "\n".join(result)

    def _truncate(self, text: str, token_limit: int) -> str:
        """Truncate *text* to fit within *token_limit*."""
        max_chars = int(token_limit * self._chars_per_token)
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n[...截断...]"
