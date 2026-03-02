"""CaptureManager -- orchestrates context sources, VLM analysis, and memory persistence."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from perception.sources.base import CaptureFrame, ContextSource
from soul.models import ModelRegistry
from soul.cognition import LLMCognitiveStep
from soul.core import MemoryEntry
from runtime.config_manager import config

logger = logging.getLogger("perception.capture")

_VLM_SYSTEM_PROMPT = (
    "You are Gazer's Digital Eye. Inspect the image and describe "
    "the active application, user task, and any notable details. "
    "Keep it concise (1-2 sentences). "
    "Language rule: respond in the same language as the user message. "
    "If the user's language is unclear, default to Simplified Chinese."
)


class CaptureManager:
    """Orchestrates one or more :class:`ContextSource` instances.

    Responsibilities:

    1. **Passive loop** -- periodically polls every registered source and,
       when a new frame arrives, runs VLM analysis then persists the result
       into ``MemoryManager``.
    2. **On-demand capture** -- ``get_latest_observation()`` grabs the newest
       frame from the *preferred* source (or any available) and returns a
       VLM description immediately, useful for tool calls.

    Parameters
    ----------
    memory_manager : MemoryManager
        Where VLM-analysed observations are persisted.
    capture_interval : float
        Seconds between passive capture cycles (default 60).
    """

    def __init__(
        self,
        memory_manager,
        capture_interval: float = 60.0,
    ) -> None:
        self._memory = memory_manager
        self._interval = capture_interval
        self._sources: Dict[str, ContextSource] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # VLM for image analysis
        self._vlm_unavailable_reason: str = ""
        api_key, base_url, model_name, _headers = ModelRegistry.resolve_model("fast_brain")
        if not self._is_fast_brain_vision_capable():
            self._vlm = None
            self._vlm_unavailable_reason = (
                "Vision model is not configured for fast_brain. "
                "Set agents.defaults.model.fallbacks[0] to an image-capable model."
            )
            logger.warning(self._vlm_unavailable_reason)
        else:
            self._vlm = LLMCognitiveStep(
                name="CaptureEye",
                model=model_name or "gpt-4o",
                api_key=api_key,
                base_url=base_url,
            )

    # ------------------------------------------------------------------
    # Source registration
    # ------------------------------------------------------------------
    def register_source(self, source: ContextSource) -> None:
        key = f"{source.source_type}:{source.source_id}"
        self._sources[key] = source
        logger.info(f"Registered context source: {key}")

    def get_source(self, source_type: str, source_id: str) -> Optional[ContextSource]:
        return self._sources.get(f"{source_type}:{source_id}")

    @property
    def sources(self) -> List[ContextSource]:
        return list(self._sources.values())

    def get_observe_capability(self) -> tuple[bool, str]:
        """Return whether screen.observe is currently available and why if not."""
        has_screen_source = any(src.source_type == "screen" for src in self._sources.values())
        if not has_screen_source:
            return (
                False,
                "Screen perception is not available: no screen source is registered.",
            )
        if self._vlm is None:
            return (
                False,
                self._vlm_unavailable_reason or "Vision model unavailable.",
            )
        return True, ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        for src in self._sources.values():
            await src.start()
        self._running = True
        self._task = asyncio.create_task(self._capture_loop())
        logger.info(
            f"CaptureManager started with {len(self._sources)} source(s), "
            f"interval={self._interval}s."
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        for src in self._sources.values():
            await src.stop()
        logger.info("CaptureManager stopped.")

    # ------------------------------------------------------------------
    # Passive capture loop
    # ------------------------------------------------------------------
    async def _capture_loop(self) -> None:
        while self._running:
            for key, src in self._sources.items():
                try:
                    frame = await src.capture()
                    if frame is None:
                        continue
                    description = await self._analyze(frame)
                    if description:
                        await self._persist(frame, description)
                except Exception as exc:
                    logger.error(f"Capture loop error for {key}: {exc}")
            await asyncio.sleep(self._interval)

    # ------------------------------------------------------------------
    # On-demand observation (used by ScreenObserveTool)
    # ------------------------------------------------------------------
    async def get_latest_observation(
        self,
        query: str = "Describe the active window and what the user is doing.",
        preferred_source: Optional[str] = None,
    ) -> str:
        """Grab a frame and return a VLM description.

        Parameters
        ----------
        query : str
            Prompt for the VLM.
        preferred_source : str | None
            Key like ``"screen:local"``; falls back to first available.
        """
        capable, reason = self.get_observe_capability()
        if not capable:
            return reason
        frame = await self._grab_frame(preferred_source)
        if frame is None:
            return "Screen perception unavailable: no frame captured from screen sources."
        return await self._analyze(frame, prompt=query) or "Failed to analyze screen."

    async def get_latest_observation_structured(
        self,
        query: str = "Describe the active window and what the user is doing.",
        preferred_source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Grab a frame and return structured UI-grounding friendly observation payload."""
        capable, reason = self.get_observe_capability()
        if not capable:
            return {
                "summary": reason,
                "query": query,
                "frame": {
                    "source_type": "screen",
                    "source_id": "unknown",
                    "timestamp": datetime.now().isoformat(),
                    "width": 0,
                    "height": 0,
                },
                "elements": [],
            }
        frame = await self._grab_frame(preferred_source)
        if frame is None:
            return {
                "summary": "Screen perception unavailable: no frame captured from screen sources.",
                "query": query,
                "frame": {
                    "source_type": "screen",
                    "source_id": "unknown",
                    "timestamp": datetime.now().isoformat(),
                    "width": 0,
                    "height": 0,
                },
                "elements": [],
            }
        summary = await self._analyze(frame, prompt=query) or "Failed to analyze screen."
        return self._build_structured_observation(frame=frame, summary=summary, query=query)

    @staticmethod
    def _build_structured_observation(*, frame: CaptureFrame, summary: str, query: str) -> Dict[str, Any]:
        width, height = frame.image.size if getattr(frame, "image", None) is not None else (0, 0)
        normalized = str(summary or "").strip()
        lines = [
            item.strip()
            for item in re.split(r"[\n\r]+|[。！？!?;；]+", normalized)
            if item and item.strip()
        ]
        lines = lines[:6]

        elements: List[Dict[str, Any]] = []
        if lines:
            row_count = max(1, len(lines))
            row_height = max(1, (height // row_count) if height > 0 else 1)
            for idx, line in enumerate(lines):
                y = (idx * row_height) if height > 0 else 0
                if height > 0:
                    y = min(y, max(0, height - 1))
                box_height = row_height if height <= 0 else min(row_height, max(1, height - y))
                confidence = round(max(0.35, 0.82 - (idx * 0.08)), 2)
                elements.append(
                    {
                        "type": "text_block",
                        "text": line,
                        "coordinates": {
                            "x": 0,
                            "y": int(y),
                            "width": int(max(0, width)),
                            "height": int(max(0, box_height)),
                        },
                        "confidence": confidence,
                    }
                )
        else:
            elements.append(
                {
                    "type": "screen_summary",
                    "text": normalized or "No observation text available.",
                    "coordinates": {
                        "x": 0,
                        "y": 0,
                        "width": int(max(0, width)),
                        "height": int(max(0, height)),
                    },
                    "confidence": 0.35,
                }
            )

        return {
            "summary": normalized or "No observation text available.",
            "query": str(query or "").strip(),
            "frame": {
                "source_type": frame.source_type,
                "source_id": frame.source_id,
                "timestamp": frame.timestamp.isoformat(),
                "width": int(max(0, width)),
                "height": int(max(0, height)),
            },
            "elements": elements,
        }

    async def _grab_frame(self, preferred: Optional[str] = None) -> Optional[CaptureFrame]:
        if preferred and preferred in self._sources:
            return await self._sources[preferred].capture()
        # Try screen sources first, then anything else
        for key, src in self._sources.items():
            if src.source_type == "screen":
                frame = await src.capture()
                if frame:
                    return frame
        for src in self._sources.values():
            frame = await src.capture()
            if frame:
                return frame
        return None

    # ------------------------------------------------------------------
    # VLM analysis
    # ------------------------------------------------------------------
    async def _analyze(
        self,
        frame: CaptureFrame,
        prompt: str = "What is active right now?",
    ) -> Optional[str]:
        if self._vlm is None:
            return self._vlm_unavailable_reason or "Vision model unavailable."
        b64 = frame.to_base64()
        try:
            result = await self._vlm.process_with_image(
                prompt=prompt,
                image_base64=b64,
                system_prompt=_VLM_SYSTEM_PROMPT,
            )
            return result.strip() if result else None
        except Exception as exc:
            logger.error(f"VLM analysis failed: {exc}")
            return None

    @staticmethod
    def _is_fast_brain_vision_capable() -> bool:
        """Check whether configured fast_brain model declares image support."""
        provider_name, model_name = ModelRegistry.resolve_model_ref("fast_brain")
        provider_name = str(provider_name or "").strip()
        model_name = str(model_name or "").strip()
        if not provider_name or not model_name:
            return False
        provider_cfg = ModelRegistry.get_provider_config(provider_name)
        models = provider_cfg.get("models")
        if not isinstance(models, list):
            # If provider registry doesn't declare capabilities, allow runtime attempt.
            return True
        for entry in models:
            if not isinstance(entry, dict):
                continue
            eid = str(entry.get("id") or entry.get("name") or "").strip()
            if eid != model_name:
                continue
            inputs = entry.get("input")
            if not isinstance(inputs, list):
                return True
            normalized = {str(item).strip().lower() for item in inputs if str(item).strip()}
            return "image" in normalized
        # Model not listed; allow runtime attempt.
        return True

    # ------------------------------------------------------------------
    # Memory persistence
    # ------------------------------------------------------------------
    async def _persist(self, frame: CaptureFrame, description: str) -> None:
        entry = MemoryEntry(
            sender="system:perception",
            content=f"[{frame.source_type}:{frame.source_id}] {description}",
            timestamp=frame.timestamp,
        )
        try:
            await self._memory.save_entry(entry)
        except Exception as exc:
            logger.error(f"Failed to persist observation: {exc}")
