"""GUI model adapter for model-driven action suggestion with safe fallback."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def _sanitize_step(raw: Any, default_target: str = "") -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    action = str(raw.get("action", "") or "").strip()
    if not action:
        return None
    args = raw.get("args", {})
    if not isinstance(args, dict):
        args = {}
    target = str(raw.get("target", default_target) or "").strip()
    return {
        "action": action,
        "args": dict(args),
        "target": target,
    }


class GUIModelAdapter:
    """Adapter that decouples GUI action planning from execution."""

    def __init__(
        self,
        *,
        planner: Optional[Callable[..., Dict[str, Any]]] = None,
        adapter_name: str = "builtin_fallback",
    ) -> None:
        self._planner = planner
        self._adapter_name = str(adapter_name or "builtin_fallback")

    def suggest_actions(
        self,
        *,
        goal: str,
        steps: Optional[List[Dict[str, Any]]] = None,
        target: str = "",
        max_steps: int = 12,
        conservative_mode: bool = False,
    ) -> Dict[str, Any]:
        safe_limit = max(1, min(int(max_steps), 30))
        requested_steps = list(steps) if isinstance(steps, list) else []

        if callable(self._planner):
            try:
                planned = self._planner(
                    goal=str(goal or ""),
                    steps=requested_steps,
                    target=str(target or ""),
                    max_steps=safe_limit,
                    conservative_mode=bool(conservative_mode),
                )
                payload = planned if isinstance(planned, dict) else {}
                model_steps = payload.get("steps", []) if isinstance(payload.get("steps"), list) else []
                normalized: List[Dict[str, Any]] = []
                for item in model_steps[:safe_limit]:
                    step = _sanitize_step(item, default_target=target)
                    if step is not None:
                        normalized.append(step)
                if normalized:
                    return {
                        "adapter": self._adapter_name,
                        "mode": "model_suggested",
                        "used_fallback": False,
                        "steps": normalized,
                        "note": str(payload.get("note", "")).strip(),
                    }
            except Exception as exc:
                # Fail open to fallback adapter path.
                fallback_note = f"planner_error:{exc}"
            else:
                fallback_note = "planner_empty_output"
        else:
            fallback_note = "planner_not_configured"

        normalized_requested: List[Dict[str, Any]] = []
        for item in requested_steps[:safe_limit]:
            step = _sanitize_step(item, default_target=target)
            if step is not None:
                normalized_requested.append(step)
        if normalized_requested:
            return {
                "adapter": self._adapter_name,
                "mode": "passthrough",
                "used_fallback": True,
                "steps": normalized_requested,
                "note": fallback_note,
            }

        # Safe fallback when no external planner and no explicit steps.
        return {
            "adapter": self._adapter_name,
            "mode": "fallback_observe_only",
            "used_fallback": True,
            "steps": [
                {
                    "action": "screen.observe",
                    "args": {"query": str(goal or "").strip() or "observe current UI state"},
                    "target": str(target or "").strip(),
                }
            ],
            "note": fallback_note,
        }


_DEFAULT_GUI_MODEL_ADAPTER: Optional[GUIModelAdapter] = None


def get_gui_model_adapter() -> GUIModelAdapter:
    global _DEFAULT_GUI_MODEL_ADAPTER
    if _DEFAULT_GUI_MODEL_ADAPTER is None:
        _DEFAULT_GUI_MODEL_ADAPTER = GUIModelAdapter()
    return _DEFAULT_GUI_MODEL_ADAPTER
