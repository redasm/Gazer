"""Tool planner: dependency-aware scheduling + tool-result compaction."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Set

from tools.batching import ToolBatchPlan, ToolBatchPlanner


_DEFAULT_DEPENDENCY_KEYS = {
    "depends_on",
    "dependson",
    "after",
    "requires",
    "input_from",
    "from_call_id",
    "source_call_id",
    "parent_call_id",
}


@dataclass
class ToolPlannerPlan:
    """Execution plan returned by tool planner."""

    batch_plan: ToolBatchPlan = field(default_factory=ToolBatchPlan)
    dependency_levels: List[List[str]] = field(default_factory=list)
    dependency_edges: int = 0
    used_dependency_scheduler: bool = False
    cycle_detected: bool = False


class ToolPlanner:
    """Dependency-aware tool-call planner with result compaction helpers."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        dependency_keys: List[str] | None = None,
        compact_results: bool = True,
        max_result_chars: int = 2400,
        error_max_result_chars: int = 4000,
        head_chars: int = 900,
        tail_chars: int = 700,
    ) -> None:
        self.enabled = bool(enabled)
        normalized_keys = dependency_keys or list(_DEFAULT_DEPENDENCY_KEYS)
        self.dependency_keys: Set[str] = {
            str(item).strip().lower()
            for item in normalized_keys
            if str(item).strip()
        }
        if not self.dependency_keys:
            self.dependency_keys = set(_DEFAULT_DEPENDENCY_KEYS)

        self.compact_results = bool(compact_results)
        self.max_result_chars = max(256, int(max_result_chars or 2400))
        self.error_max_result_chars = max(512, int(error_max_result_chars or 4000))
        self.head_chars = max(64, int(head_chars or 900))
        self.tail_chars = max(64, int(tail_chars or 700))

    @staticmethod
    def _normalize_dependency_value(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw = [value]
        elif isinstance(value, list):
            raw = [str(item) for item in value]
        elif isinstance(value, dict):
            raw = []
            for key in ("id", "call_id", "tool_call_id"):
                item = value.get(key)
                if item is not None:
                    raw.append(str(item))
        else:
            raw = [str(value)]
        out: List[str] = []
        for item in raw:
            marker = str(item).strip()
            if not marker:
                continue
            if "," in marker:
                out.extend([seg.strip() for seg in marker.split(",") if seg.strip()])
            else:
                out.append(marker)
        return out

    def _extract_dependencies(self, *, call_id: str, arguments: Any, valid_call_ids: Set[str]) -> Set[str]:
        if not isinstance(arguments, dict):
            return set()
        deps: Set[str] = set()
        for key, value in arguments.items():
            key_norm = str(key).strip().lower()
            if key_norm not in self.dependency_keys:
                continue
            for dep in self._normalize_dependency_value(value):
                if dep == call_id:
                    continue
                if dep in valid_call_ids:
                    deps.add(dep)
        return deps

    @staticmethod
    def _build_empty_plan() -> ToolPlannerPlan:
        return ToolPlannerPlan(batch_plan=ToolBatchPlan())

    def plan(
        self,
        tool_calls: List[Any],
        *,
        lane_resolver: Callable[[str], str],
        max_parallel_calls: int,
        batch_planner: ToolBatchPlanner,
    ) -> ToolPlannerPlan:
        calls = list(tool_calls or [])
        if not calls:
            return self._build_empty_plan()

        if not self.enabled or len(calls) == 1:
            base = batch_planner.plan(
                calls,
                lane_resolver=lane_resolver,
                max_parallel_calls=max_parallel_calls,
            )
            return ToolPlannerPlan(batch_plan=base)

        call_by_id: Dict[str, Any] = {}
        ordered_ids: List[str] = []
        for idx, call in enumerate(calls):
            call_id = str(getattr(call, "id", "") or "").strip()
            if not call_id:
                call_id = f"auto_{idx}"
                setattr(call, "id", call_id)
            if call_id not in call_by_id:
                ordered_ids.append(call_id)
            call_by_id[call_id] = call
        valid_call_ids = set(call_by_id.keys())
        order_index = {call_id: idx for idx, call_id in enumerate(ordered_ids)}

        deps_by_call: Dict[str, Set[str]] = {}
        dependency_edges = 0
        for call_id in ordered_ids:
            call = call_by_id[call_id]
            deps = self._extract_dependencies(
                call_id=call_id,
                arguments=getattr(call, "arguments", {}),
                valid_call_ids=valid_call_ids,
            )
            deps_by_call[call_id] = deps
            dependency_edges += len(deps)

        indegree: Dict[str, int] = {call_id: len(deps_by_call.get(call_id, set())) for call_id in ordered_ids}
        dependents: Dict[str, List[str]] = {call_id: [] for call_id in ordered_ids}
        for call_id, deps in deps_by_call.items():
            for dep in deps:
                dependents.setdefault(dep, []).append(call_id)

        ready: List[str] = sorted(
            [call_id for call_id, degree in indegree.items() if degree == 0],
            key=lambda item: order_index[item],
        )
        seen: Set[str] = set()
        levels: List[List[str]] = []
        while ready:
            level = list(ready)
            levels.append(level)
            for current in level:
                seen.add(current)
            next_ready: List[str] = []
            for current in level:
                for child in dependents.get(current, []):
                    indegree[child] = max(0, indegree[child] - 1)
                    if indegree[child] == 0 and child not in seen:
                        next_ready.append(child)
            ready = sorted(set(next_ready), key=lambda item: order_index[item])

        cycle_detected = False
        if len(seen) < len(ordered_ids):
            cycle_detected = True
            remain = [call_id for call_id in ordered_ids if call_id not in seen]
            if remain:
                levels.append(remain)

        all_batches: List[List[Any]] = []
        duplicate_of: Dict[str, str] = {}
        deduped_calls = 0
        requested_calls = len(ordered_ids)
        for level in levels:
            level_calls = [call_by_id[call_id] for call_id in level]
            level_plan = batch_planner.plan(
                level_calls,
                lane_resolver=lane_resolver,
                max_parallel_calls=max_parallel_calls,
            )
            all_batches.extend(level_plan.batches)
            duplicate_of.update(level_plan.duplicate_of)
            deduped_calls += int(level_plan.deduped_calls)

        unique_calls = max(0, requested_calls - deduped_calls)
        batch_plan = ToolBatchPlan(
            batches=all_batches,
            duplicate_of=duplicate_of,
            requested_calls=requested_calls,
            unique_calls=unique_calls,
            deduped_calls=deduped_calls,
        )
        return ToolPlannerPlan(
            batch_plan=batch_plan,
            dependency_levels=levels,
            dependency_edges=dependency_edges,
            used_dependency_scheduler=dependency_edges > 0,
            cycle_detected=cycle_detected,
        )

    @staticmethod
    def _stringify_result(result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, (dict, list)):
            try:
                return json.dumps(result, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                return str(result)
        return str(result)

    def compact_tool_result(self, *, tool_name: str, result: Any) -> str:
        text = self._stringify_result(result)
        if not self.compact_results:
            return text

        is_error = text.lstrip().startswith("Error [")
        limit = self.error_max_result_chars if is_error else self.max_result_chars
        if len(text) <= limit:
            return text

        head = min(self.head_chars, max(64, limit // 2))
        tail_limit = max(64, limit - head - 96)
        tail = min(self.tail_chars, tail_limit)
        if head + tail >= len(text):
            return text[:limit]

        omitted = len(text) - head - tail
        marker = (
            f"[planner_compacted tool={str(tool_name or 'unknown')} "
            f"omitted_chars={omitted}]"
        )
        return f"{text[:head]}\n\n{marker}\n\n{text[-tail:]}"
