from __future__ import annotations

"""Evolution & feedback router — persona evolution management."""

import csv
import io
import math
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from tools.admin.state import config, logger, get_trajectory_store, get_evolution, get_personality
from .auth import verify_admin_token

router = APIRouter(tags=["evolution"])

MAX_SYSTEM_PROMPT_CHARS = 32_000


def _clamp(value: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    """Convert *value* to float, reject non-finite, and clamp to [lo, hi]."""
    f = float(value)
    if not math.isfinite(f):
        raise ValueError(f"non-finite value: {value!r}")
    return max(lo, min(hi, f))


class OceanUpdate(BaseModel):
    openness: Optional[float] = None
    conscientiousness: Optional[float] = None
    extraversion: Optional[float] = None
    agreeableness: Optional[float] = None
    neuroticism: Optional[float] = None
    humor_level: Optional[float] = None
    verbosity: Optional[float] = None
    formality: Optional[float] = None

    @field_validator("*", mode="before")
    @classmethod
    def clamp_zero_one(cls, v: Any) -> Any:
        if v is None:
            return v
        return _clamp(v)


class UpdatePersonalityRequest(BaseModel):
    ocean: Optional[OceanUpdate] = None
    system_prompt: Optional[str] = Field(None, max_length=MAX_SYSTEM_PROMPT_CHARS)


@router.get("/personality/state", dependencies=[Depends(verify_admin_token)])
async def get_personality_state():
    """Return live personality state: OCEAN vector, affect, mental state, goals."""
    personality = get_personality()
    if personality is None:
        return {"status": "unavailable", "reason": "personality_not_initialized"}

    affect = personality.affect_manager.current_affect()
    return {
        "status": "ok",
        "ocean": personality.personality.to_dict(),
        "affect": {
            "label": affect.to_label(),
            **affect.to_dict(),
        },
        "mental_state": {
            "name": personality.current_state.name,
            "description": personality.current_state.description,
        },
        "goal_progress": personality.get_goal_progress(),
        "system_prompt": str(config.get("personality.system_prompt", "") or ""),
    }


@router.post("/personality/state", dependencies=[Depends(verify_admin_token)])
async def update_personality_state(data: UpdatePersonalityRequest):
    """Update OCEAN personality vector and/or system prompt."""
    personality = get_personality()
    if personality is None:
        return {"status": "unavailable", "reason": "personality_not_initialized"}

    if data.ocean is not None:
        ocean = data.ocean
        from soul.personality.personality_vector import PersonalityVector
        current = personality.personality
        personality.personality = PersonalityVector(
            openness=ocean.openness if ocean.openness is not None else current.openness,
            conscientiousness=ocean.conscientiousness if ocean.conscientiousness is not None else current.conscientiousness,
            extraversion=ocean.extraversion if ocean.extraversion is not None else current.extraversion,
            agreeableness=ocean.agreeableness if ocean.agreeableness is not None else current.agreeableness,
            neuroticism=ocean.neuroticism if ocean.neuroticism is not None else current.neuroticism,
            humor_level=ocean.humor_level if ocean.humor_level is not None else current.humor_level,
            verbosity=ocean.verbosity if ocean.verbosity is not None else current.verbosity,
            formality=ocean.formality if ocean.formality is not None else current.formality,
            learning_rate=current.learning_rate,
        )
        # Recompute affect baseline from updated personality
        personality.affect_manager.update_baseline(
            personality.personality.to_affect_baseline()
        )

    if data.system_prompt is not None:
        prompt = data.system_prompt.strip()
        config.set("personality.system_prompt", prompt)
        # Sync back to SOUL.md so restarts don't revert the change
        _sync_prompt_to_soul_md(prompt)

    return {"status": "ok", "ocean": personality.personality.to_dict()}


def _sync_prompt_to_soul_md(prompt: str) -> None:
    """Best-effort write-back of system_prompt to assets/SOUL.md."""
    import os
    candidates = [
        Path(os.getcwd()) / "assets" / "SOUL.md",
        Path(__file__).resolve().parents[3] / "assets" / "SOUL.md",
    ]
    for path in candidates:
        if path.is_file():
            try:
                path.write_text(prompt, encoding="utf-8")
                logger.info("System prompt synced back to %s", path)
            except OSError as exc:
                logger.warning("Failed to sync prompt to %s: %s", path, exc)
            return


@router.post("/feedback", dependencies=[Depends(verify_admin_token)])
async def submit_feedback(data: Dict[str, Any]):
    """Submit user feedback for persona evolution.

    data: {"label": "positive/negative", "feedback": "...", "context": "..."}
    """
    label = str(data.get("label", "unknown"))
    feedback_text = str(data.get("feedback", ""))
    context = str(data.get("context", "web_console"))
    run_id = str(data.get("run_id", "")).strip()
    session_key = str(data.get("session_key", "")).strip()
    chat_id = str(data.get("chat_id", "")).strip()

    attached = False
    attached_run_id: Optional[str] = None
    if get_trajectory_store() is not None:
        if not run_id:
            run_id = get_trajectory_store().resolve_latest_run(
                session_key=session_key or None,
                chat_id=chat_id or None,
            ) or ""
        if run_id:
            attached = bool(
                get_trajectory_store().add_feedback(
                    run_id,
                    label=label,
                    feedback=feedback_text,
                    context=context,
                    metadata={
                        "session_key": session_key or None,
                        "chat_id": chat_id or None,
                    },
                )
            )
            if attached:
                attached_run_id = run_id

    evolution = get_evolution()
    evolution.collect_feedback(label, context, feedback_text)
    auto_optimize = await evolution.maybe_auto_optimize(trigger="feedback")
    return {
        "status": "feedback_received",
        "attached_to_trajectory": attached,
        "run_id": attached_run_id,
        "auto_optimize": auto_optimize,
    }


@router.get("/evolution/stats", dependencies=[Depends(verify_admin_token)])
async def get_evolution_stats():
    """Get feedback statistics."""
    evolution = get_evolution()
    stats = evolution.get_feedback_stats()
    current_prompt = config.get("personality.system_prompt", "")
    return {
        **stats,
        "current_prompt": current_prompt,
        "auto_optimize": evolution.get_auto_optimize_status(),
    }


@router.post("/evolution/optimize", dependencies=[Depends(verify_admin_token)])
async def trigger_evolution():
    """Trigger persona evolution optimization cycle."""
    updated = await get_evolution().optimize_persona()
    new_prompt = config.get("personality.system_prompt", "") if updated else None
    return {"updated": updated, "new_prompt": new_prompt}


@router.get("/evolution/history", dependencies=[Depends(verify_admin_token)])
async def get_evolution_history(
    limit: int = 50,
    event: Optional[str] = None,
    reason: Optional[str] = None,
    format: str = "json",
):
    evolution = get_evolution()
    max_limit = max(1, min(int(limit or 50), 500))
    items = evolution.get_recent_history(limit=500)
    if event:
        event_norm = str(event).strip().lower()
        items = [item for item in items if str(item.get("event", "")).strip().lower() == event_norm]
    if reason:
        reason_norm = str(reason).strip().lower()
        items = [item for item in items if str(item.get("reason", "")).strip().lower() == reason_norm]
    items = items[-max_limit:]
    fmt = str(format or "json").strip().lower()
    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["timestamp", "event", "attempted", "updated", "reason", "duration_ms"])
        for item in items:
            writer.writerow(
                [
                    item.get("timestamp", ""),
                    item.get("event", ""),
                    item.get("attempted", ""),
                    item.get("updated", ""),
                    item.get("reason", ""),
                    item.get("duration_ms", ""),
                ]
            )
        return PlainTextResponse(buffer.getvalue(), media_type="text/csv")
    return {"status": "ok", "items": items, "total": len(items)}


@router.get("/evolution/history/summary", dependencies=[Depends(verify_admin_token)])
async def get_evolution_history_summary():
    evolution = get_evolution()
    return {"status": "ok", "summary": evolution.get_history_summary()}


@router.post("/evolution/history/clear", dependencies=[Depends(verify_admin_token)])
async def clear_evolution_history():
    evolution = get_evolution()
    cleared = evolution.clear_history()
    return {"status": "ok", "cleared": int(cleared)}
