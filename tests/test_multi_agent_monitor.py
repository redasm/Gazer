import asyncio

import pytest

from multi_agent.monitor import MultiAgentMonitorHub, monitor_hub
from tools.admin import multi_agent_monitor as monitor_api
from tools.admin import ROUTERS


@pytest.mark.asyncio
async def test_monitor_hub_emits_session_init_and_task_events():
    hub = MultiAgentMonitorHub()
    queue = await hub.subscribe()

    try:
        await hub.begin_session("sess-1", "Analyze edge AI market")
        init_event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert init_event["event"] == "session.init"
        assert init_event["payload"]["session_key"] == "sess-1"
        assert init_event["payload"]["session_label"] == "Analyze edge AI market"

        await hub.task_created(
            session_key="sess-1",
            task_id="t1",
            title="Research market size",
            description="Collect TAM data",
            agent_id="research",
            depends=[],
            priority="normal",
        )
        created_event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert created_event["event"] == "task.created"
        assert created_event["payload"]["task_id"] == "t1"

        snapshot = await hub.build_session_init_payload("sess-1")
        assert snapshot["tasks"][0]["task_id"] == "t1"
        assert snapshot["tasks"][0]["status"] == "queued"
    finally:
        await hub.unsubscribe(queue)


@pytest.mark.asyncio
async def test_monitor_hub_add_comment_updates_task_and_emits_event():
    hub = MultiAgentMonitorHub()
    queue = await hub.subscribe()

    try:
        await hub.begin_session("sess-2", "Build report")
        await queue.get()
        await hub.task_created(
            session_key="sess-2",
            task_id="t2",
            title="Write summary",
            description="Summarize findings",
            agent_id="writer",
            depends=[],
            priority="high",
        )
        await queue.get()

        comment = await hub.add_comment("t2", text="Please include exact numbers", author="User")
        comment_event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert comment_event["event"] == "task.comment"
        assert comment["task_id"] == "t2"
        assert comment["text"] == "Please include exact numbers"

        snapshot = await hub.build_session_init_payload("sess-2")
        assert snapshot["tasks"][0]["comments"][0]["text"] == "Please include exact numbers"
    finally:
        await hub.unsubscribe(queue)


@pytest.mark.asyncio
async def test_comment_api_uses_monitor_hub_state():
    await monitor_hub.reset()
    await monitor_hub.begin_session("sess-api", "Review task")
    await monitor_hub.task_created(
        session_key="sess-api",
        task_id="t-api",
        title="Review code",
        description="Check regressions",
        agent_id="reviewer",
        depends=[],
        priority="normal",
    )

    response = await monitor_api.post_task_comment(
        "t-api",
        {"text": "Need a stronger failure analysis", "author": "Owner"},
    )

    assert response["status"] == "ok"
    assert response["comment"]["author"] == "Owner"

    snapshot = await monitor_hub.build_session_init_payload("sess-api")
    assert snapshot["tasks"][0]["comments"][0]["text"] == "Need a stronger failure analysis"


def test_admin_router_list_includes_multi_agent_monitor_router():
    tags = [tuple(router_tags) for _router, _prefix, router_tags in ROUTERS if _router is not None]
    assert ("multi-agent-monitor",) in tags
