"""CronTool -- allows the agent to manage cron jobs programmatically."""

import json
import logging
from dataclasses import asdict
from typing import Any, Dict

from tools.base import Tool, ToolSafetyTier

logger = logging.getLogger("CronTool")


class CronTool(Tool):
    """Manage scheduled (cron) jobs.

    Actions: list, add, remove, edit, run.
    """

    def __init__(self, scheduler: Any) -> None:
        """*scheduler* must be a ``CronScheduler`` instance."""
        self._scheduler = scheduler

    @property
    def name(self) -> str:
        return "cron"

    @property
    def provider(self) -> str:
        return "cron"

    @property
    def description(self) -> str:
        return (
            "Manage scheduled cron jobs. Actions: "
            "'list' (show all jobs), "
            "'add' (create a new job: name, cron_expr, message, agent_id, one_shot), "
            "'remove' (delete by job_id), "
            "'edit' (update fields of a job by job_id), "
            "'run' (force-run a job immediately by job_id)."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "add", "remove", "edit", "run"],
                    "description": "Cron management action.",
                },
                "job_id": {
                    "type": "string",
                    "description": "Job ID (for remove/edit/run).",
                },
                "name": {
                    "type": "string",
                    "description": "Human-readable name for the job (for add).",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "5-field cron expression: minute hour day month weekday (for add/edit).",
                },
                "message": {
                    "type": "string",
                    "description": "Message to inject as user prompt when job runs (for add/edit).",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Target agent ID (for add/edit, default: main).",
                },
                "one_shot": {
                    "type": "boolean",
                    "description": "If true, delete the job after it runs once (for add).",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Enable/disable the job (for edit).",
                },
            },
            "required": ["action"],
        }

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.STANDARD

    @staticmethod
    def _error(code: str, message: str) -> str:
        return f"Error [{code}]: {message}"

    async def execute(self, action: str = "", **kwargs: Any) -> str:
        dispatch = {
            "list": self._list,
            "add": self._add,
            "remove": self._remove,
            "edit": self._edit,
            "run": self._run,
        }
        handler = dispatch.get(action)
        if not handler:
            return self._error("CRON_ACTION_UNKNOWN", f"unknown action '{action}'.")
        return await handler(**kwargs)

    async def _list(self, **_: Any) -> str:
        jobs = self._scheduler.list_jobs()
        if not jobs:
            return "No cron jobs configured."
        lines = []
        for j in jobs:
            status = "enabled" if j.enabled else "disabled"
            lines.append(f"- {j.id}: {j.name} [{j.cron_expr}] ({status}) agent={j.agent_id}")
        return "\n".join(lines)

    async def _add(self, **kwargs: Any) -> str:
        from scheduler.cron import CronJob
        name = kwargs.get("name", "Unnamed")
        cron_expr = kwargs.get("cron_expr", "")
        message = kwargs.get("message", "")
        if not cron_expr or not message:
            return self._error("CRON_ADD_ARGS_REQUIRED", "'cron_expr' and 'message' are required for add.")

        job = CronJob(
            name=name,
            cron_expr=cron_expr,
            message=message,
            agent_id=kwargs.get("agent_id", "main"),
            one_shot=kwargs.get("one_shot", False),
        )
        self._scheduler.add(job)
        return f"Created cron job: {job.id} ({job.name}) [{job.cron_expr}]"

    async def _remove(self, **kwargs: Any) -> str:
        job_id = kwargs.get("job_id", "")
        if not job_id:
            return self._error("CRON_REMOVE_ID_REQUIRED", "'job_id' is required for remove.")
        if self._scheduler.remove(job_id):
            return f"Removed cron job: {job_id}"
        return f"Job not found: {job_id}"

    async def _edit(self, **kwargs: Any) -> str:
        job_id = kwargs.get("job_id", "")
        if not job_id:
            return self._error("CRON_EDIT_ID_REQUIRED", "'job_id' is required for edit.")
        updates = {
            k: v for k, v in kwargs.items()
            if k in ("name", "cron_expr", "message", "agent_id", "enabled", "one_shot")
            and v is not None
        }
        job = self._scheduler.edit(job_id, **updates)
        if not job:
            return f"Job not found: {job_id}"
        return f"Updated cron job: {job.id} ({job.name})"

    async def _run(self, **kwargs: Any) -> str:
        job_id = kwargs.get("job_id", "")
        if not job_id:
            return self._error("CRON_RUN_ID_REQUIRED", "'job_id' is required for run.")
        result = await self._scheduler.force_run(job_id)
        if result is None:
            return f"Job not found: {job_id}"
        return f"Job {job_id} executed. Result:\n{result}"
