from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from devices.models import NodeActionResult, NodeInfo

logger = logging.getLogger("DeviceRegistry")


class DeviceNode(ABC):
    @property
    @abstractmethod
    def node_id(self) -> str:
        pass

    @property
    def backend(self) -> str:
        """Execution backend identifier for observability and routing."""
        return "python"

    @abstractmethod
    def info(self) -> NodeInfo:
        pass

    @abstractmethod
    async def invoke(self, action: str, args: Dict[str, Any]) -> NodeActionResult:
        pass


class DeviceRegistry:
    def __init__(self, default_target: str = "") -> None:
        self._nodes: Dict[str, DeviceNode] = {}
        self._default_target = default_target.strip()

    def register(self, node: DeviceNode) -> None:
        self._nodes[node.node_id] = node
        logger.info("Registered device node: %s", node.node_id)

    def unregister(self, node_id: str) -> None:
        if node_id in self._nodes:
            del self._nodes[node_id]
            logger.info("Unregistered device node: %s", node_id)

    def get(self, node_id: str) -> Optional[DeviceNode]:
        return self._nodes.get(node_id)

    @property
    def default_target(self) -> str:
        return self._default_target

    @default_target.setter
    def default_target(self, value: str) -> None:
        self._default_target = (value or "").strip()

    def resolve_target(self, target: Optional[str] = None) -> str:
        raw_target = (target or "").strip()
        if raw_target:
            return raw_target
        if self._default_target:
            return self._default_target
        if len(self._nodes) == 1:
            return next(iter(self._nodes))
        return ""

    def list_nodes(self) -> List[Dict[str, Any]]:
        return [node.info().to_dict() for node in self._nodes.values()]

    def describe_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        node = self._nodes.get(node_id)
        if not node:
            return None
        return node.info().to_dict()

    async def invoke(
        self,
        *,
        action: str,
        args: Optional[Dict[str, Any]] = None,
        target: Optional[str] = None,
    ) -> NodeActionResult:
        resolved_target = self.resolve_target(target)
        if not resolved_target:
            return NodeActionResult(
                ok=False,
                code="DEVICE_TARGET_REQUIRED",
                message="No node target specified and no default target configured.",
            )

        node = self._nodes.get(resolved_target)
        if not node:
            return NodeActionResult(
                ok=False,
                code="DEVICE_TARGET_NOT_FOUND",
                message=f"Node '{resolved_target}' not found.",
            )

        node_info = node.info()
        supported_actions = {cap.action for cap in node_info.capabilities}
        if action not in supported_actions:
            return NodeActionResult(
                ok=False,
                code="DEVICE_ACTION_UNSUPPORTED",
                message=f"Action '{action}' is not supported by node '{resolved_target}'.",
            )

        try:
            return await node.invoke(action=action, args=args or {})
        except Exception as exc:
            logger.exception("Node invoke failed: target=%s, action=%s", resolved_target, action)
            return NodeActionResult(
                ok=False,
                code="DEVICE_INVOKE_EXCEPTION",
                message=f"Node invocation failed: {exc}",
            )
