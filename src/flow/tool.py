"""FlowRunTool — agent-facing tool for GazerFlow workflows.

Exposes ``run``, ``resume``, ``list``, and ``status`` actions so the
orchestrator can trigger deterministic pipelines from natural language.
"""

import json
import logging
from typing import Any, Dict

from tools.base import Tool

logger = logging.getLogger("FlowRunTool")


class FlowRunTool(Tool):
    """Agent tool wrapping :class:`FlowEngine`.

    Actions:
    - ``list``   — show available workflows
    - ``status`` — show persisted state for a flow
    - ``run``    — start a workflow with given args
    - ``resume`` — continue past an approval gate
    - ``recover`` — resume from latest interruption checkpoint
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    @property
    def name(self) -> str:
        return "run_flow"

    @property
    def description(self) -> str:
        return (
            "Execute a deterministic GazerFlow workflow. "
            "Actions: list (show flows), status (flow state), "
            "run (start a flow), resume (continue past approval gate), "
            "recover (resume from interruption checkpoint)."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["run", "resume", "list", "status", "recover"],
                    "description": "Which action to perform.",
                },
                "flow_name": {
                    "type": "string",
                    "description": "Name of the flow (for run / status).",
                },
                "args": {
                    "type": "object",
                    "description": "Arguments to pass when action is 'run'.",
                },
                "resume_token": {
                    "type": "string",
                    "description": "Token returned by a needs_approval result (for resume).",
                },
            },
            "required": ["action"],
        }

    async def execute(self, **kwargs: Any) -> str:
        action = kwargs.get("action", "list")
        access_sender_is_owner = kwargs.get("_access_sender_is_owner")
        access_policy = kwargs.get("_access_policy")

        try:
            if action == "list":
                flows = self._engine.list_flows()
                return json.dumps({"flows": flows}, ensure_ascii=False, indent=2)

            elif action == "status":
                flow_name = kwargs.get("flow_name", "")
                if not flow_name:
                    return json.dumps({"error": "flow_name required for status"})
                info = self._engine.status(flow_name)
                return json.dumps(info, ensure_ascii=False, indent=2, default=str)

            elif action == "run":
                flow_name = kwargs.get("flow_name", "")
                if not flow_name:
                    return json.dumps({"error": "flow_name required for run"})
                args = kwargs.get("args", {})
                result = await self._engine.run(
                    flow_name,
                    args,
                    sender_is_owner=access_sender_is_owner,
                    policy=access_policy,
                )
                return json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str)

            elif action == "resume":
                token = kwargs.get("resume_token", "")
                if not token:
                    return json.dumps({"error": "resume_token required for resume"})
                result = await self._engine.resume(
                    token,
                    sender_is_owner=access_sender_is_owner,
                    policy=access_policy,
                )
                return json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str)

            elif action == "recover":
                flow_name = kwargs.get("flow_name", "")
                if not flow_name:
                    return json.dumps({"error": "flow_name required for recover"})
                result = await self._engine.resume_interrupted(
                    flow_name,
                    sender_is_owner=access_sender_is_owner,
                    policy=access_policy,
                )
                return json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str)

            else:
                return json.dumps({"error": f"Unknown action: {action}"})

        except Exception as exc:
            logger.exception("FlowRunTool error")
            return json.dumps({"error": str(exc)})
