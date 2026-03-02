"""Simple GUI capability benchmark for click/type/switch/confirm flows."""

from __future__ import annotations

import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional


GuiActionInvoker = Callable[[str, Dict[str, Any], str], Awaitable[Dict[str, Any]]]


def build_default_gui_simple_cases() -> List[Dict[str, Any]]:
    """Return default lightweight GUI baseline cases."""
    return [
        {
            "id": "click_primary",
            "name": "Click primary area",
            "category": "click",
            "action": "input.mouse.click",
            "args": {"x": 80, "y": 80, "verify_after": True, "verify_settle_seconds": 0.0},
        },
        {
            "id": "type_short_text",
            "name": "Type short text",
            "category": "type",
            "action": "input.keyboard.type",
            "args": {"text": "gazer benchmark"},
        },
        {
            "id": "switch_window",
            "name": "Switch page/window",
            "category": "switch",
            "action": "input.keyboard.hotkey",
            "args": {"keys": ["alt", "tab"]},
        },
        {
            "id": "confirm_action",
            "name": "Confirm action",
            "category": "confirm",
            "action": "input.keyboard.hotkey",
            "args": {"keys": ["enter"]},
        },
    ]


class GuiSimpleBenchmarkRunner:
    """Run repeatable simple GUI action baseline and emit observability report."""

    VERSION = "gui-simple-benchmark.v1"

    def __init__(self, invoker: GuiActionInvoker) -> None:
        self._invoker = invoker

    @staticmethod
    def _normalize_case(raw: Dict[str, Any], idx: int) -> Dict[str, Any]:
        item = raw if isinstance(raw, dict) else {}
        action = str(item.get("action", "")).strip()
        args = item.get("args", {})
        category = str(item.get("category", "custom")).strip().lower() or "custom"
        return {
            "id": str(item.get("id", f"case_{idx}")).strip() or f"case_{idx}",
            "name": str(item.get("name", action or f"case_{idx}")).strip() or f"case_{idx}",
            "category": category,
            "action": action,
            "args": args if isinstance(args, dict) else {},
            "target": str(item.get("target", "")).strip(),
        }

    @staticmethod
    def _normalize_invocation_result(result: Dict[str, Any]) -> Dict[str, Any]:
        payload = result if isinstance(result, dict) else {}
        return {
            "ok": bool(payload.get("ok", False)),
            "code": str(payload.get("code", "")).strip(),
            "message": str(payload.get("message", "")).strip(),
            "raw": payload.get("raw", payload),
        }

    async def run(
        self,
        *,
        target: str = "",
        cases: Optional[List[Dict[str, Any]]] = None,
        stop_on_failure: bool = False,
    ) -> Dict[str, Any]:
        selected_raw = cases if isinstance(cases, list) and cases else build_default_gui_simple_cases()
        selected = [self._normalize_case(item, idx + 1) for idx, item in enumerate(selected_raw)]
        run_started = time.time()
        run_id = f"gui_bench_{int(run_started * 1000)}_{uuid.uuid4().hex[:6]}"

        steps: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        by_category: Dict[str, Dict[str, Any]] = {}

        for idx, case in enumerate(selected, start=1):
            action = str(case.get("action", "")).strip()
            args = case.get("args", {}) if isinstance(case.get("args"), dict) else {}
            case_target = str(case.get("target", "")).strip() or str(target or "").strip()
            step_started = time.perf_counter()
            if not action:
                normalized = {"ok": False, "code": "GUI_BENCHMARK_INVALID_ACTION", "message": "missing action", "raw": {}}
            else:
                raw_result = await self._invoker(action, args, case_target)
                normalized = self._normalize_invocation_result(raw_result)
            latency_ms = round((time.perf_counter() - step_started) * 1000.0, 2)
            status = "ok" if normalized["ok"] else "failed"

            step = {
                "step_index": idx,
                "case_id": case["id"],
                "name": case["name"],
                "category": case["category"],
                "action": action,
                "args": args,
                "target": case_target,
                "status": status,
                "ok": bool(normalized["ok"]),
                "code": normalized["code"],
                "message": normalized["message"],
                "latency_ms": latency_ms,
            }
            steps.append(step)

            bucket = by_category.setdefault(
                case["category"],
                {"category": case["category"], "total": 0, "passed": 0, "failed": 0, "success_rate": 0.0},
            )
            bucket["total"] += 1
            if normalized["ok"]:
                bucket["passed"] += 1
            else:
                bucket["failed"] += 1
                failures.append(step)
                if stop_on_failure:
                    break

        total = len(steps)
        passed = sum(1 for item in steps if bool(item.get("ok", False)))
        failed = total - passed
        for item in by_category.values():
            item["success_rate"] = round(float(item["passed"]) / max(1, int(item["total"])), 4)

        failure_reasons: Dict[str, int] = {}
        for item in failures:
            code = str(item.get("code", "")).strip() or "UNKNOWN"
            failure_reasons[code] = int(failure_reasons.get(code, 0)) + 1
        sorted_reasons = sorted(
            ({"code": code, "count": int(count)} for code, count in failure_reasons.items()),
            key=lambda x: (-int(x["count"]), str(x["code"])),
        )

        return {
            "run_id": run_id,
            "version": self.VERSION,
            "generated_at": run_started,
            "target": str(target or "").strip(),
            "total_cases": total,
            "passed_cases": passed,
            "failed_cases": failed,
            "success_rate": round(float(passed) / max(1, total), 4),
            "by_category": sorted(by_category.values(), key=lambda x: str(x["category"])),
            "failure_reasons": sorted_reasons,
            "steps": steps,
            "replay_dataset": [
                {
                    "case_id": item.get("case_id", ""),
                    "category": item.get("category", ""),
                    "action": item.get("action", ""),
                    "args": item.get("args", {}),
                    "target": item.get("target", ""),
                }
                for item in steps
            ],
        }

