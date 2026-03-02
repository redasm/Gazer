"""Gazer's personality module — cognitive state machine.

This module defines the mental states and cognitive processing pipeline.
It no longer owns its own MemoryManager or ToolRegistry instances;
those are injected by the caller (typically GazerAgent) to avoid
duplicate resource allocation and keep `soul/` as a pure cognitive layer.
"""

from soul.core import MentalState, WorkingMemory, MemoryEntry
from soul.memory.working_context import WorkingContext
from soul.cognition import LLMCognitiveStep
from soul.trust import TrustSystem
from soul.affect.affective_state import AffectiveState
from soul.affect.emotional_event import AffectiveStateManager, EmotionalEvent
from runtime.config_manager import config
from soul.llm_adapter import AsyncOpenAIAdapter
from tools.registry import ToolRegistry
from llm.base import LLMProvider, LLMResponse
from collections import deque
from typing import Any, Optional, TYPE_CHECKING
import logging
import json

if TYPE_CHECKING:
    from memory.manager import MemoryManager

logger = logging.getLogger("GazerSoul")

# Consolidation trigger threshold
_CONSOLIDATION_THRESHOLD = 20




class GazerPersonality:
    """Gazer's core personality (cognitive state machine).

    Dependencies are injected rather than created internally:
    - memory_manager: shared MemoryManager from GazerAgent
    - tool_registry: shared ToolRegistry from AgentLoop

    No longer inherits ``MentalProcess``.  The state machine is inlined
    to decouple from the legacy ``soul.core`` hierarchy (Issue-02/03).
    """

    IDLE = MentalState(name="IDLE", description="Waiting, silently observing")
    INTERACTING = MentalState(name="INTERACTING", description="Actively conversing")
    THINKING = MentalState(name="THINKING", description="Deep thought / reflection")

    def __init__(
        self,
        memory_manager: "MemoryManager",
        tool_registry: Optional[ToolRegistry] = None,
        llm_provider: Optional[LLMProvider] = None,
    ) -> None:
        states, initial_state_name, transitions = self._load_mental_process_config()
        initial_state = states.get(initial_state_name) or states.get("IDLE") or self.IDLE
        # Inline state machine (formerly MentalProcess.__init__)
        self.current_state: MentalState = initial_state
        self.state_history: deque[MentalState] = deque([initial_state], maxlen=100)
        # Shared instances (no longer created here)
        self.memory_manager = memory_manager
        self.tool_registry = tool_registry or ToolRegistry()
        self._states = states
        self._on_input_transition = transitions
        self.trust_system = TrustSystem()
        self._goal_progress_state: dict[str, Any] = {
            "turns": 0,
            "successful_replies": 0,
            "goal_mentions": {},
        }

        # [NEW Phase 1-3 Components]
        from soul.memory.memory_port import EmotionAwareMemoryPort, OpenVikingMemoryPort
        from soul.affect.emotional_event import AffectiveStateManager
        from soul.personality.personality_vector import PersonalityVector
        from soul.cognitive.proactive_inference_engine import ProactiveInferenceEngine
        from soul.cognitive.context_budget_manager import ContextBudgetManager

        # 1. PersonalityVector first — it drives the emotional baseline
        self.personality = PersonalityVector()

        # 2. MemoryPort (with emotion-aware decorator)
        raw_port = OpenVikingMemoryPort(self.memory_manager.backend)
        self.memory_port = EmotionAwareMemoryPort(raw_port)

        # 3. AffectiveStateManager with personality baseline + memory_port
        #    for consolidation (Fix #6 + #7)
        self.affect_manager = AffectiveStateManager(
            baseline=self.personality.to_affect_baseline(),
            memory_port=self.memory_port,
        )

        self.proactive_engine = ProactiveInferenceEngine()
        self.budget_manager = ContextBudgetManager()

        # 4. SessionDistiller + IdentityConstitution (Issue-06 Layer-2 + Issue-11)
        from soul.personality.identity_constitution import IdentityConstitution
        from soul.personality.evolution_service import SessionDistiller

        self._constitution = IdentityConstitution(
            llm_client=None, enable_soft_check=False  # hard bounds only for now
        )
        self._session_distiller = SessionDistiller(
            llm_client=None,  # wired later when LLM is resolved
            memory_port=self.memory_port,
            constitution=self._constitution,
        )
        self._session_feedback: list = []  # collects FeedbackEvents during session
        # [/NEW Phase 1-3 Components]

        from soul.consolidation import NightlyConsolidator
        self.consolidator = NightlyConsolidator(
            self.memory_manager,
            self.memory_manager.relationships,
            self.memory_manager.emotions,
            llm_provider=llm_provider,
        )

        from soul.models import ModelRegistry
        api_key, base_url, model_name, headers = ModelRegistry.resolve_model("slow_brain")

        # Prefer the injected LLMProvider (handles openai-responses, litellm, etc.)
        self._llm_provider: Optional[LLMProvider] = llm_provider
        self._llm_model: Optional[str] = model_name

        if not llm_provider and not api_key:
            logger.warning("SlowBrain API_KEY is not set. Behavior may be limited.")

        # Legacy cognitive step: only created as fallback when no provider injected
        self.legacy_cognitive_step: Optional[LLMCognitiveStep] = None
        if not llm_provider and api_key:
            self.legacy_cognitive_step = LLMCognitiveStep(
                name="GazerCognition",
                model=model_name,
                api_key=api_key,
                base_url=base_url,
                default_headers=headers,
            )

        # Wire LLM client into SessionDistiller
        if self.legacy_cognitive_step is not None:
            self._session_distiller.set_llm_client(AsyncOpenAIAdapter(self.legacy_cognitive_step.client, model_name))

        self.system_prompt: str = config.get(
            "personality.system_prompt", "You are Gazer, an AI companion."
        )

    @staticmethod
    def _resolve_drives_and_goals() -> tuple[list[str], list[str]]:
        drives_raw = config.get("personality.drives", []) or []
        goals_raw = config.get("personality.goals", []) or []
        drives = [str(item).strip() for item in drives_raw if str(item).strip()] if isinstance(drives_raw, list) else []
        goals = [str(item).strip() for item in goals_raw if str(item).strip()] if isinstance(goals_raw, list) else []
        return drives, goals

    def _build_motivation_context(self) -> str:
        drives, goals = self._resolve_drives_and_goals()
        if not drives and not goals:
            return ""
        parts = ["## Drives & Goals"]
        if drives:
            parts.append("Drives:")
            parts.extend([f"- {item}" for item in drives])
        if goals:
            parts.append("Current Goals:")
            parts.extend([f"- {item}" for item in goals])
        progress_block = self._build_goal_progress_block(goals)
        if progress_block:
            parts.append(progress_block)
        return "\n".join(parts)

    def _build_goal_progress_block(self, goals: list[str]) -> str:
        state = self._goal_progress_state
        if not isinstance(state, dict):
            return ""
        turns = int(state.get("turns", 0) or 0)
        if turns <= 0:
            return ""
        successful_replies = int(state.get("successful_replies", 0) or 0)
        mentions = state.get("goal_mentions", {})
        if not isinstance(mentions, dict):
            mentions = {}
        lines = [
            "Goal Progress:",
            f"- turn_success_rate: {successful_replies}/{turns}",
        ]
        for goal in goals:
            lines.append(f"- {goal}: mentions={int(mentions.get(goal, 0) or 0)}")
        return "\n".join(lines)

    @staticmethod
    def _goal_tokens(goal: str) -> list[str]:
        normalized = str(goal or "").strip().lower().replace("_", " ").replace("-", " ")
        return [token for token in normalized.split() if len(token) >= 3]

    def _update_goal_progress(self, user_text: str, reply_text: str) -> None:
        state = self._goal_progress_state
        if not isinstance(state, dict):
            state = {}
            self._goal_progress_state = state
        state["turns"] = int(state.get("turns", 0) or 0) + 1
        if str(reply_text or "").strip():
            state["successful_replies"] = int(state.get("successful_replies", 0) or 0) + 1

        _, goals = self._resolve_drives_and_goals()
        mentions = state.get("goal_mentions", {})
        if not isinstance(mentions, dict):
            mentions = {}
        haystack = f"{user_text or ''} {reply_text or ''}".lower()
        for goal in goals:
            tokens = self._goal_tokens(goal)
            if not tokens:
                continue
            if any(token in haystack for token in tokens):
                mentions[goal] = int(mentions.get(goal, 0) or 0) + 1
        state["goal_mentions"] = mentions

    def reset_goal_progress(self) -> None:
        """Reset goal progress counters (useful for tests)."""
        self._goal_progress_state = {
            "turns": 0,
            "successful_replies": 0,
            "goal_mentions": {},
        }

    def transition_to(self, next_state: MentalState) -> None:
        """Transition to a new mental state."""
        self.current_state = next_state
        self.state_history.append(next_state)

    @classmethod
    def _load_mental_process_config(cls) -> tuple[dict[str, MentalState], str, dict[str, str]]:
        cfg = config.get("personality.mental_process", {}) or {}
        states_cfg = cfg.get("states", [])
        states: dict[str, MentalState] = {}
        if isinstance(states_cfg, list):
            for item in states_cfg:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip().upper()
                if not name:
                    continue
                desc = str(item.get("description", "")).strip() or name
                states[name] = MentalState(name=name, description=desc)
        if not states:
            states = {
                "IDLE": cls.IDLE,
                "INTERACTING": cls.INTERACTING,
                "THINKING": cls.THINKING,
            }
        initial_state_name = str(cfg.get("initial_state", "IDLE")).strip().upper() or "IDLE"
        transitions_cfg = cfg.get("on_input_transition", {})
        transitions: dict[str, str] = {}
        if isinstance(transitions_cfg, dict):
            for src, dst in transitions_cfg.items():
                src_name = str(src).strip().upper()
                dst_name = str(dst).strip().upper()
                if src_name and dst_name and dst_name in states:
                    transitions[src_name] = dst_name
        if not transitions:
            transitions = {"IDLE": "INTERACTING", "THINKING": "INTERACTING"}
        return states, initial_state_name, transitions

    async def _run_llm(
        self, prompt: str, system_prompt: str, tools: list = None
    ) -> MemoryEntry:
        """Run LLM via injected provider or legacy cognitive step."""
        if self._llm_provider is not None:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
            try:
                resp: LLMResponse = await self._llm_provider.chat(
                    messages, tools=tools, model=self._llm_model
                )
            except Exception as e:
                logger.error("LLM provider call failed: %s", e)
                return MemoryEntry(sender="Gazer", content="[System Error: Cognitive Failure]")

            metadata: dict = {}
            if resp.tool_calls:
                metadata = {
                    "tool_calls": [
                        {"name": tc.name, "args": tc.arguments}
                        for tc in resp.tool_calls
                    ]
                }
            return MemoryEntry(
                sender="Gazer",
                content=resp.content or "",
                metadata=metadata,
            )

        # Legacy fallback via LLMCognitiveStep (raw AsyncOpenAI)
        if self.legacy_cognitive_step is not None:
            wm = WorkingMemory(
                owner="Gazer",
                memories=[MemoryEntry(sender="user", content=prompt)],
            )
            return await self.legacy_cognitive_step.run(wm, system_prompt, tools=tools)

        return MemoryEntry(sender="Gazer", content="[System Error: No LLM configured]")

    async def process(self, context: WorkingContext) -> WorkingContext:
        """Run one cognitive cycle: perceive → think → act → remember.

        Takes and returns an immutable ``WorkingContext`` (Issue-02).
        Uses ``ContextBudgetManager.build_prompt()`` for prompt assembly
        (replaces the old ``AdapterCognitiveStep`` inline class).

        **Memory persistence**: this method persists both the incoming
        user message and the generated reply to ``MemoryManager``
        before returning.  Callers should NOT persist these entries
        again to avoid duplication.
        """
        logger.info(f"Current State: {self.current_state.name}")

        user_input = context.user_input
        if not user_input:
            return context

        next_state_name = self._on_input_transition.get(self.current_state.name.upper())
        if next_state_name and next_state_name in self._states:
            self.transition_to(self._states[next_state_name])

        response_text: Optional[str] = None
        has_llm = self._llm_provider is not None or self.legacy_cognitive_step is not None

        if has_llm:
            current_system_prompt = config.get(
                "personality.system_prompt", self.system_prompt
            )

            # 0. Inject current affect
            context = context.with_update(affect=self.affect_manager.current_affect())

            # 1. Companion context (memories, relationships, emotions)
            base_memory = self.memory_manager.load_recent(limit=20)
            companion_context = await self.memory_manager.get_companion_context(
                user_input, base_memory
            )

            # 2. Trust context
            sender_id = str(context.get_metadata("sender_id") or "").strip()
            social_context = self.trust_system.get_relationship_prompt(sender_id)

            # Populate WorkingContext slots
            tools_desc = self.tool_registry.get_definitions()
            motivation_context = self._build_motivation_context()

            agent_ctx = list(context.agent_context)
            if motivation_context:
                agent_ctx.append(motivation_context)
            if "Available Tools:" not in current_system_prompt:
                agent_ctx.append(f"Available Tools:\n{json.dumps(tools_desc, indent=2)}")

            user_ctx = list(context.user_context)
            user_ctx.extend([f"Relationship Trust: {social_context}", companion_context])

            context = context.with_update(
                agent_context=tuple(agent_ctx),
                user_context=tuple(user_ctx),
            )

            # 3. Proactive Inference (pass affect history)
            signals = await self.proactive_engine.infer(
                context,
                affect_history=self.affect_manager.get_history(),
            )
            context = self.proactive_engine.inject_hints(context, signals)

            # 4. Build prompt via ContextBudgetManager (replaces AdapterCognitiveStep)
            prompt = self.budget_manager.build_prompt(
                context, personality=self.personality
            )

            # Run LLM
            response_entry = await self._run_llm(
                prompt, current_system_prompt, tools=tools_desc
            )
            context = context.with_update(turn_count=context.turn_count + 1)

            # 5. Tool-call loop
            max_tool_rounds_raw = config.get("personality.runtime.max_tool_rounds", 3)
            try:
                max_tool_rounds = max(1, int(max_tool_rounds_raw or 3))
            except (TypeError, ValueError):
                max_tool_rounds = 3

            tool_rounds = 0
            while (
                isinstance(response_entry.metadata, dict)
                and response_entry.metadata.get("tool_calls")
                and tool_rounds < max_tool_rounds
            ):
                tool_rounds += 1
                tool_calls = response_entry.metadata.get("tool_calls") or []
                if not isinstance(tool_calls, list):
                    break

                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    tool_name = str(tc.get("name", "")).strip()
                    tool_args = tc.get("args", {})
                    if not tool_name:
                        continue
                    if not isinstance(tool_args, dict):
                        tool_args = {}

                    logger.info(f"AI requested tool: {tool_name} with {tool_args}")
                    try:
                        result = await self.tool_registry.execute(
                            tool_name,
                            tool_args,
                            sender_id=sender_id,
                            channel=str(context.get_metadata("channel") or ""),
                        )
                    except Exception as exc:
                        logger.warning("Tool execution failed for %s: %s", tool_name, exc)
                        result = f"Error: {exc}"

                    tool_result_str = f"Tool Execution [{tool_name}] Result: {result}"
                    context = context.with_update(
                        session_context=context.session_context + (tool_result_str,)
                    )

                    tool_result = MemoryEntry(
                        sender="System",
                        content=tool_result_str,
                        importance=0.6,
                    )
                    await self.memory_manager.save_entry(tool_result)

                # Rebuild prompt and re-run LLM
                prompt = self.budget_manager.build_prompt(
                    context, personality=self.personality
                )
                response_entry = await self._run_llm(
                    prompt, current_system_prompt, tools=tools_desc
                )
                context = context.with_update(turn_count=context.turn_count + 1)

            if (
                isinstance(response_entry.metadata, dict)
                and response_entry.metadata.get("tool_calls")
                and tool_rounds >= max_tool_rounds
            ):
                logger.warning(
                    "Tool-call rounds reached limit (%d); returning latest response.",
                    max_tool_rounds,
                )

            response_text = response_entry.content if response_entry else ""

            # 6. Connect Affect Event (meaningful delta from emotion analysis)
            affect_delta, trigger_key = await self._analyze_affect_delta(
                user_input, response_text,
            )
            half_life = AffectiveStateManager.HALF_LIFE_MAP.get(
                trigger_key,
                AffectiveStateManager.HALF_LIFE_MAP["default"],
            )
            self.affect_manager.add_event(EmotionalEvent(
                trigger=trigger_key,
                affect_delta=affect_delta,
                half_life_seconds=half_life,
            ))

            # 7. Memory consolidation (still uses legacy WorkingMemory internally)
            try:
                recent = self.memory_manager.load_recent(limit=_CONSOLIDATION_THRESHOLD + 5)
                if len(recent.memories) > _CONSOLIDATION_THRESHOLD:
                    logger.info("Triggering memory consolidation...")
                    summary = await self.consolidator.summarize_interactions(recent)
                    self.consolidator.update_long_term_memory(recent, summary)
            except Exception as exc:
                logger.warning("Memory consolidation failed (non-fatal): %s", exc)
        else:
            response_text = f"Received: {user_input}. (LLM Key not set)"

        if response_text:
            self._update_goal_progress(user_input, response_text)

        # Persist entries
        incoming_entry = MemoryEntry(
            sender="user",
            content=user_input,
            metadata={
                "sender_id": str(context.get_metadata("sender_id") or ""),
                "channel": str(context.get_metadata("channel") or ""),
                "chat_id": str(context.get_metadata("chat_id") or ""),
            },
        )
        await self.memory_manager.save_entry(incoming_entry)
        if response_text:
            await self.memory_manager.save_entry(
                MemoryEntry(sender="Gazer", content=response_text)
            )

        # Flush any deferred emotional consolidation writes
        await self.affect_manager.flush_consolidations()

        return context.set_metadata("reply", response_text or "")

    # ------------------------------------------------------------------
    # Emotion-to-affect bridge (Fix #9)
    # ------------------------------------------------------------------

    # Emotion → arousal / dominance mapping for VAD construction
    _EMOTION_AROUSAL: dict[str, float] = {
        "happy": 0.3, "excited": 0.6, "calm": -0.3, "neutral": 0.0,
        "tired": -0.4, "anxious": 0.4, "sad": -0.2, "angry": 0.5,
    }
    _EMOTION_DOMINANCE: dict[str, float] = {
        "happy": 0.1, "excited": 0.2, "calm": 0.0, "neutral": 0.0,
        "tired": -0.2, "anxious": -0.3, "sad": -0.3, "angry": 0.3,
    }
    _EMOTION_TRIGGER: dict[str, str] = {
        "happy": "user_praise", "excited": "user_praise",
        "sad": "user_criticism", "angry": "user_criticism",
        "anxious": "user_criticism", "tired": "default",
        "calm": "default", "neutral": "default",
    }

    async def _analyze_affect_delta(
        self, user_text: str, reply_text: str
    ) -> tuple["AffectiveState", str]:
        """Derive a meaningful ``AffectiveState`` delta from user text.

        Uses the ``EmotionAnalyzer`` (LLM with keyword fallback) to map
        the user's message into a VAD delta vector.

        Returns:
            ``(affect_delta, trigger_key)`` where *trigger_key* is suitable
            for looking up in ``AffectiveStateManager.HALF_LIFE_MAP``.
        """
        analyzer = self.memory_manager.emotions.analyzer

        # Try LLM analysis first, fall back to keyword-based
        emotion, sentiment = "neutral", 0.0
        try:
            llm_result = await analyzer.analyze_with_llm(user_text)
            if llm_result:
                emotion, sentiment, _ = llm_result
            else:
                emotion, sentiment = analyzer.analyze(user_text)
        except Exception:
            emotion, sentiment = analyzer.analyze(user_text)

        valence = max(-1.0, min(1.0, sentiment))
        arousal = self._EMOTION_AROUSAL.get(emotion, 0.0)
        dominance = self._EMOTION_DOMINANCE.get(emotion, 0.0)
        trigger_key = self._EMOTION_TRIGGER.get(emotion, "default")

        return AffectiveState(valence=valence, arousal=arousal, dominance=dominance), trigger_key

    # ------------------------------------------------------------------
    # Session lifecycle (Fix #13)
    # ------------------------------------------------------------------

    async def on_session_end(
        self, transcript: list[dict[str, Any]]
    ) -> None:
        """Called when a conversation session ends.

        Triggers Layer-2 personality evolution via ``SessionDistiller``.
        """
        from soul.personality.evolution_service import FeedbackEvent

        if not self._session_distiller.has_llm:
            logger.debug("SessionDistiller has no LLM; skipping distillation.")
            return

        feedback = [
            FeedbackEvent(positive=f.get("positive", True), content=f.get("content", ""))
            for f in self._session_feedback
            if isinstance(f, dict)
        ]
        try:
            new_personality = await self._session_distiller.distill_session(
                transcript=transcript,
                feedback_events=feedback,
                current_personality=self.personality,
            )
            self.personality = new_personality
            # Recompute affect baseline from updated personality
            self.affect_manager.update_baseline(self.personality.to_affect_baseline())
            logger.info("Personality distilled after session end.")
        except Exception as exc:
            logger.warning("Session distillation failed: %s", exc)
        finally:
            self._session_feedback.clear()
