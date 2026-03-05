from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from agent.gui_model_adapter import GUIModelAdapter, get_gui_model_adapter
from tools.base import Tool
from tools.media_marker import MEDIA_MARKER

if TYPE_CHECKING:
    from devices.registry import DeviceRegistry


class DeviceToolBase(Tool):
    def __init__(
        self,
        registry: Optional["DeviceRegistry"] = None,
        gui_adapter: Optional[GUIModelAdapter] = None,
    ) -> None:
        self._registry = registry
        self._gui_adapter = gui_adapter

    @property
    def provider(self) -> str:
        return "devices"

    @staticmethod
    def _error(code: str, message: str) -> str:
        return f"Error [{code}]: {message}"


class NodeListTool(DeviceToolBase):
    @property
    def name(self) -> str:
        return "node_list"


    @property
    def description(self) -> str:
        return "List available execution nodes and their capabilities."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **_: Any) -> str:
        if self._registry is None:
            return self._error("DEVICE_REGISTRY_UNINITIALIZED", "Device registry is not initialized.")
        payload = {
            "nodes": self._registry.list_nodes(),
            "default_target": self._registry.default_target,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


class NodeDescribeTool(DeviceToolBase):
    @property
    def name(self) -> str:
        return "node_describe"


    @property
    def description(self) -> str:
        return "Describe a specific node, including supported actions."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Node ID. Optional when a default target exists.",
                },
            },
        }

    async def execute(self, target: str = "", **_: Any) -> str:
        if self._registry is None:
            return self._error("DEVICE_REGISTRY_UNINITIALIZED", "Device registry is not initialized.")
        resolved_target = self._registry.resolve_target(target)
        if not resolved_target:
            return self._error(
                "DEVICE_TARGET_REQUIRED",
                "No target provided and no default node is configured.",
            )
        data = self._registry.describe_node(resolved_target)
        if data is None:
            return self._error("DEVICE_TARGET_NOT_FOUND", f"Node '{resolved_target}' not found.")
        return json.dumps(data, ensure_ascii=False, indent=2)


class NodeInvokeTool(DeviceToolBase):
    @property
    def name(self) -> str:
        return "node_invoke"

    @property
    def owner_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Invoke a capability on a target node. Use node_list/node_describe first "
            "to inspect available targets and actions. Typical actions include "
            "`screen.observe`, `screen.screenshot`, `file.send`, and input controls."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "Action name, e.g. `screen.screenshot`, `file.send`, "
                        "`screen.observe`, or `input.mouse.click`."
                    ),
                },
                "args": {
                    "type": "object",
                    "description": (
                        "Action parameters as a JSON object. "
                        "For `file.send`, you MUST provide `{\"path\": \"<absolute_path>\"}`."
                    ),
                },
                "target": {
                    "type": "string",
                    "description": "Node ID. Optional when default target is configured.",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        args: Optional[Dict[str, Any]] = None,
        target: str = "",
        **_: Any,
    ) -> str:
        if self._registry is None:
            return self._error("DEVICE_REGISTRY_UNINITIALIZED", "Device registry is not initialized.")
        if not isinstance(args, dict) and args is not None:
            return self._error("DEVICE_ARGS_INVALID", "'args' must be an object.")
        result = await self._registry.invoke(
            action=action,
            args=args or {},
            target=target,
        )
        if not result.ok:
            code = result.code or "DEVICE_INVOKE_FAILED"
            return self._error(code, result.message)

        media_path = str(result.data.get("media_path", "")).strip() if result.data else ""
        if media_path:
            return f"{result.message} {MEDIA_MARKER}{media_path}"

        if result.data:
            return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)

        return result.message


class GuiTaskExecuteTool(DeviceToolBase):
    @property
    def name(self) -> str:
        return "gui_task_execute"

    @property
    def owner_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Execute GUI task steps with observe->act->observe->corrective loop. "
            "On first action failure, automatically switches to conservative mode "
            "(observe-only, no click/type actions)."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "Task goal summary for the execution trace.",
                },
                "steps": {
                    "type": "array",
                    "description": "Ordered GUI actions: [{action, args, target?}]",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "args": {"type": "object"},
                            "target": {"type": "string"},
                        },
                        "required": ["action"],
                    },
                },
                "target": {
                    "type": "string",
                    "description": "Optional default target node ID for all steps.",
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Maximum steps to execute (1-30).",
                },
                "conservative_mode": {
                    "type": "boolean",
                    "description": "Force observe-only conservative mode from start.",
                },
            },
            "required": ["goal"],
        }

    async def execute(
        self,
        goal: str,
        steps: Optional[List[Dict[str, Any]]] = None,
        target: str = "",
        max_steps: int = 12,
        conservative_mode: bool = False,
        **_: Any,
    ) -> str:
        if self._registry is None:
            return self._error("DEVICE_REGISTRY_UNINITIALIZED", "Device registry is not initialized.")

        effective_max_steps = max(1, min(int(max_steps), 30))
        run_id = f"gui_{int(time.time() * 1000)}"
        trace: List[Dict[str, Any]] = []
        current_conservative = bool(conservative_mode)
        switched_reason = ""
        adapter = self._gui_adapter or get_gui_model_adapter()
        plan = adapter.suggest_actions(
            goal=str(goal or ""),
            steps=steps if isinstance(steps, list) else [],
            target=target,
            max_steps=effective_max_steps,
            conservative_mode=current_conservative,
        )
        planned_steps = plan.get("steps", []) if isinstance(plan.get("steps"), list) else []
        if not planned_steps:
            return self._error("GUI_STEPS_INVALID", "No valid GUI steps after adapter planning.")

        for idx, raw_step in enumerate(planned_steps[:effective_max_steps], start=1):
            if not isinstance(raw_step, dict):
                trace.append(
                    {
                        "step_index": idx,
                        "status": "invalid_step",
                        "error": "step item must be object",
                    }
                )
                if not current_conservative:
                    current_conservative = True
                    switched_reason = "invalid_step"
                continue

            action = str(raw_step.get("action", "") or "").strip()
            action_args = raw_step.get("args", {})
            step_target = str(raw_step.get("target", target) or "").strip()
            if not isinstance(action_args, dict):
                action_args = {}
            observe_before = await self._registry.invoke(
                action="screen.observe",
                args={"query": f"[step {idx}] before action: {action or 'unknown'}"},
                target=step_target,
            )

            step_payload: Dict[str, Any] = {
                "step_index": idx,
                "action": action,
                "target": step_target or self._registry.default_target,
                "observe_before": observe_before.to_dict(),
            }

            if not action:
                step_payload["status"] = "invalid_step"
                step_payload["error"] = "missing action"
                trace.append(step_payload)
                if not current_conservative:
                    current_conservative = True
                    switched_reason = "missing_action"
                continue

            if current_conservative and action.startswith("input."):
                observe_after = await self._registry.invoke(
                    action="screen.observe",
                    args={"query": f"[step {idx}] conservative mode skip action {action}"},
                    target=step_target,
                )
                step_payload["status"] = "skipped_conservative"
                step_payload["observe_after"] = observe_after.to_dict()
                trace.append(step_payload)
                continue

            action_result = await self._registry.invoke(
                action=action,
                args=action_args,
                target=step_target,
            )
            step_payload["action_result"] = action_result.to_dict()
            observe_after = await self._registry.invoke(
                action="screen.observe",
                args={"query": f"[step {idx}] after action: {action}"},
                target=step_target,
            )
            step_payload["observe_after"] = observe_after.to_dict()

            if action_result.ok:
                step_payload["status"] = "ok"
                trace.append(step_payload)
                continue

            corrective = await self._registry.invoke(
                action="screen.observe",
                args={"query": f"[step {idx}] action failed, collect fallback context"},
                target=step_target,
            )
            step_payload["status"] = "failed"
            step_payload["corrective_observation"] = corrective.to_dict()
            trace.append(step_payload)
            if not current_conservative:
                current_conservative = True
                switched_reason = f"action_failed:{action}"

        summary = {
            "run_id": run_id,
            "goal": str(goal or "").strip(),
            "status": "completed",
            "conservative_mode": current_conservative,
            "conservative_switch_reason": switched_reason,
            "adapter": {
                "name": str(plan.get("adapter", "") or ""),
                "mode": str(plan.get("mode", "") or ""),
                "used_fallback": bool(plan.get("used_fallback", True)),
                "note": str(plan.get("note", "") or ""),
                "planned_steps": len(planned_steps),
                "requested_steps": len(steps) if isinstance(steps, list) else 0,
            },
            "steps_executed": len(trace),
            "steps": trace,
            "replay": [
                {
                    "step_index": int(item.get("step_index", 0) or 0),
                    "action": str(item.get("action", "") or ""),
                    "status": str(item.get("status", "") or ""),
                    "target": str(item.get("target", "") or ""),
                }
                for item in trace
            ],
        }
        failed_steps = [
            item
            for item in trace
            if str(item.get("status", "")).strip().lower() in {"failed", "invalid_step"}
        ]
        summary["benchmark_hook"] = {
            "schema": "gui-simple-benchmark-hook.v1",
            "step_total": len(trace),
            "failed_step_total": len(failed_steps),
            "failure_codes": sorted(
                {
                    str(((item.get("action_result") or {}).get("code", ""))).strip()
                    for item in failed_steps
                    if str(((item.get("action_result") or {}).get("code", ""))).strip()
                }
            ),
            "conservative_mode": bool(current_conservative),
            "conservative_switch_reason": str(switched_reason or ""),
        }
        return json.dumps(summary, ensure_ascii=False, indent=2)
