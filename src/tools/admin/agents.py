"""Admin API routes for multi-agent management.

Exposes the AgentOrchestrator state for Web Console and CLI.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from ._shared import ORCHESTRATOR, logger

router = APIRouter(tags=["agents"])


@router.get("/agents")
async def list_agents() -> List[Dict[str, Any]]:
    """List all registered agents and their status."""
    orch = ORCHESTRATOR
    if orch is None:
        return []

    result = []
    for agent_id, cfg in orch._agents.items():
        # Count active tasks for this agent
        active = sum(
            1 for r in orch._task_records.values()
            if r.agent_id == agent_id and r.status in ("queued", "running", "sleeping")
        )
        result.append({
            "id": cfg.id,
            "name": cfg.name,
            "model": cfg.model,
            "is_default": cfg.is_default,
            "has_loop": agent_id in orch._loops,
            "active_tasks": active,
        })
    return result


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> Dict[str, Any]:
    """Get details for a specific agent."""
    orch = ORCHESTRATOR
    if orch is None:
        raise HTTPException(status_code=404, detail="Orchestrator not initialized")

    cfg = orch._agents.get(agent_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    tasks = [
        r.to_public()
        for r in orch._task_records.values()
        if r.agent_id == agent_id
    ]

    bindings = [
        {"channel": b.channel, "chat_id": b.chat_id, "sender_id": b.sender_id}
        for b in orch._bindings
        if b.agent_id == agent_id
    ]

    return {
        "id": cfg.id,
        "name": cfg.name,
        "model": cfg.model,
        "workspace": str(cfg.workspace),
        "is_default": cfg.is_default,
        "tool_policy": cfg.tool_policy,
        "system_prompt_file": cfg.system_prompt_file,
        "has_loop": agent_id in orch._loops,
        "bindings": bindings,
        "tasks": tasks,
    }


@router.get("/agents/{agent_id}/tasks")
async def list_agent_tasks(agent_id: str) -> List[Dict[str, Any]]:
    """List tasks (queued/running/completed) for a specific agent."""
    orch = ORCHESTRATOR
    if orch is None:
        return []

    return [
        r.to_public()
        for r in orch._task_records.values()
        if r.agent_id == agent_id
    ]
