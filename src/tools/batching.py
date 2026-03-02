"""Tool batching planner and observability metrics for multi-tool turns."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List


@dataclass
class ToolBatchPlan:
    """A concrete execution plan for one model-emitted tool-call set."""

    batches: List[List[Any]] = field(default_factory=list)
    duplicate_of: Dict[str, str] = field(default_factory=dict)
    requested_calls: int = 0
    unique_calls: int = 0
    deduped_calls: int = 0

    @property
    def batch_groups(self) -> int:
        return len(self.batches)

    @property
    def actual_rounds(self) -> int:
        return len(self.batches)

    @property
    def parallel_rounds(self) -> int:
        return sum(1 for batch in self.batches if len(batch) > 1)


class ToolBatchPlanner:
    """Plan batch execution with duplicate call compaction."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_batch_size: int = 4,
        dedupe_enabled: bool = False,
    ) -> None:
        self.enabled = bool(enabled)
        self.max_batch_size = max(1, int(max_batch_size or 1))
        self.dedupe_enabled = bool(dedupe_enabled)

    @staticmethod
    def _stable_args(arguments: Any) -> str:
        if isinstance(arguments, (dict, list)):
            try:
                return json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            except (TypeError, ValueError):
                return repr(arguments)
        return str(arguments)

    def _signature(self, *, lane: str, name: str, arguments: Any) -> str:
        payload = f"{lane}|{name}|{self._stable_args(arguments)}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def plan(
        self,
        tool_calls: List[Any],
        *,
        lane_resolver: Callable[[str], str],
        max_parallel_calls: int,
    ) -> ToolBatchPlan:
        calls = list(tool_calls or [])
        requested = len(calls)
        if requested == 0:
            return ToolBatchPlan()

        if not self.enabled or requested == 1:
            return ToolBatchPlan(
                batches=[calls],
                requested_calls=requested,
                unique_calls=requested,
                deduped_calls=0,
            )

        signature_map: Dict[str, str] = {}
        unique_calls: List[Any] = []
        duplicate_of: Dict[str, str] = {}
        lane_order: List[str] = []
        lane_calls: Dict[str, List[Any]] = {}

        for call in calls:
            call_id = str(getattr(call, "id", "") or "")
            name = str(getattr(call, "name", "") or "").strip()
            lane = str(lane_resolver(name) or "default")
            signature = self._signature(lane=lane, name=name, arguments=getattr(call, "arguments", {}))
            if self.dedupe_enabled and signature in signature_map and call_id:
                duplicate_of[call_id] = signature_map[signature]
                continue
            if self.dedupe_enabled and call_id:
                signature_map[signature] = call_id
            unique_calls.append(call)
            if lane not in lane_calls:
                lane_calls[lane] = []
                lane_order.append(lane)
            lane_calls[lane].append(call)

        ordered_calls: List[Any] = []
        for lane in lane_order:
            ordered_calls.extend(lane_calls.get(lane, []))

        batch_size = max(1, min(self.max_batch_size, int(max_parallel_calls or 1)))
        batches: List[List[Any]] = [
            ordered_calls[idx : idx + batch_size]
            for idx in range(0, len(ordered_calls), batch_size)
        ]
        return ToolBatchPlan(
            batches=batches,
            duplicate_of=duplicate_of,
            requested_calls=requested,
            unique_calls=len(unique_calls),
            deduped_calls=max(0, requested - len(unique_calls)),
        )


class ToolBatchingTracker:
    """Aggregated metrics for tool-round efficiency."""

    __slots__ = (
        "turns",
        "turns_with_tool_calls",
        "total_tokens",
        "tool_rounds",
        "parallel_rounds",
        "tool_calls_requested",
        "tool_calls_executed",
        "deduped_calls",
        "batch_groups",
    )

    def __init__(self) -> None:
        self.turns = 0
        self.turns_with_tool_calls = 0
        self.total_tokens = 0
        self.tool_rounds = 0
        self.parallel_rounds = 0
        self.tool_calls_requested = 0
        self.tool_calls_executed = 0
        self.deduped_calls = 0
        self.batch_groups = 0

    def record_turn(
        self,
        *,
        total_tokens: int,
        tool_rounds: int,
        parallel_rounds: int,
        tool_calls_requested: int,
        tool_calls_executed: int,
        deduped_calls: int,
        batch_groups: int,
    ) -> None:
        self.turns += 1
        self.total_tokens += max(0, int(total_tokens or 0))
        self.tool_rounds += max(0, int(tool_rounds or 0))
        self.parallel_rounds += max(0, int(parallel_rounds or 0))
        self.tool_calls_requested += max(0, int(tool_calls_requested or 0))
        self.tool_calls_executed += max(0, int(tool_calls_executed or 0))
        self.deduped_calls += max(0, int(deduped_calls or 0))
        self.batch_groups += max(0, int(batch_groups or 0))
        if tool_calls_requested > 0:
            self.turns_with_tool_calls += 1

    def summary(self) -> Dict[str, Any]:
        avg_tokens_per_turn = (
            float(self.total_tokens) / float(self.turns)
            if self.turns > 0
            else 0.0
        )
        avg_tool_rounds = (
            float(self.tool_rounds) / float(self.turns_with_tool_calls)
            if self.turns_with_tool_calls > 0
            else 0.0
        )
        parallel_gain_ratio = (
            float(self.tool_calls_requested) / float(max(1, self.tool_rounds))
            if self.tool_rounds > 0
            else 1.0
        )
        dedupe_ratio = (
            float(self.deduped_calls) / float(max(1, self.tool_calls_requested))
            if self.tool_calls_requested > 0
            else 0.0
        )
        avg_batch_size = (
            float(self.tool_calls_executed) / float(max(1, self.batch_groups))
            if self.batch_groups > 0
            else 0.0
        )
        return {
            "turns": int(self.turns),
            "turns_with_tool_calls": int(self.turns_with_tool_calls),
            "avg_tokens_per_turn": round(avg_tokens_per_turn, 4),
            "avg_tool_rounds": round(avg_tool_rounds, 4),
            "parallel_gain_ratio": round(parallel_gain_ratio, 4),
            "dedupe_ratio": round(dedupe_ratio, 4),
            "avg_batch_size": round(avg_batch_size, 4),
            "totals": {
                "tokens": int(self.total_tokens),
                "tool_rounds": int(self.tool_rounds),
                "parallel_rounds": int(self.parallel_rounds),
                "tool_calls_requested": int(self.tool_calls_requested),
                "tool_calls_executed": int(self.tool_calls_executed),
                "deduped_calls": int(self.deduped_calls),
                "batch_groups": int(self.batch_groups),
            },
        }
