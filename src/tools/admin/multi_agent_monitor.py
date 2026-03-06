from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from multi_agent.monitor import monitor_hub

from .auth import _verify_ws_auth, verify_admin_token

router = APIRouter(tags=["multi-agent-monitor"])


@router.websocket("/ws/monitor")
async def multi_agent_monitor_socket(websocket: WebSocket) -> None:
    if not await _verify_ws_auth(websocket):
        return

    queue = await monitor_hub.subscribe()
    await websocket.accept()
    try:
        await websocket.send_json(await monitor_hub.build_session_init_event())
        while True:
            queue_task = asyncio.create_task(queue.get())
            recv_task = asyncio.create_task(websocket.receive_text())
            done, pending = await asyncio.wait(
                {queue_task, recv_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if queue_task in done:
                await websocket.send_json(queue_task.result())
                continue
            try:
                recv_task.result()
            except WebSocketDisconnect:
                break
    finally:
        await monitor_hub.unsubscribe(queue)


@router.post(
    "/multi-agent/tasks/{task_id}/comments",
    dependencies=[Depends(verify_admin_token)],
)
async def post_task_comment(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Comment text is required")
    author = str(payload.get("author", "User")).strip() or "User"
    try:
        comment = await monitor_hub.add_comment(task_id, text=text, author=author)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "ok",
        "comment": comment,
    }
