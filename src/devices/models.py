from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class NodeCapability:
    action: str
    description: str
    tier: str = "safe"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "description": self.description,
            "tier": self.tier,
        }


@dataclass
class NodeInfo:
    node_id: str
    kind: str
    label: str
    online: bool = True
    capabilities: List[NodeCapability] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "label": self.label,
            "online": self.online,
            "capabilities": [cap.to_dict() for cap in self.capabilities],
            "metadata": dict(self.metadata),
        }


@dataclass
class NodeActionResult:
    ok: bool
    message: str
    code: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "code": self.code,
            "data": dict(self.data),
        }
