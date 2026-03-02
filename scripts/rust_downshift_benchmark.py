from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List

import psutil

from devices.adapters.local_desktop import LocalDesktopNode
from devices.registry import DeviceRegistry
from runtime.rust_sidecar import RustShellOperations
from tools.coding import ExecTool
from tools.device_tools import NodeInvokeTool


def _p95(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * 0.95))))
    return float(ordered[idx])


class _FakeCaptureManager:
    def get_observe_capability(self):
        return True, ""

    async def get_latest_observation(self, query: str):
        return f"observed: {query}"


class _FakeDesktopRustClient:
    async def rpc(self, *, method: str, params=None, trace_id: str = ""):
        if method == "desktop.screen.screenshot":
            return {"message": "Screenshot captured (rust).", "media_path": "C:/tmp/rust_shot.png"}
        return {}


class _FakeShellRustClient:
    async def rpc(self, *, method: str, params=None, trace_id: str = ""):
        if method == "shell.exec":
            return {"exit_code": 0, "stdout": "bench\n", "stderr": ""}
        return {}


async def _bench_screenshot(mode: str, iterations: int) -> Dict[str, Any]:
    registry = DeviceRegistry(default_target="local-desktop")
    if mode == "python":
        node = LocalDesktopNode(
            capture_manager=_FakeCaptureManager(),
            action_enabled=False,
            backend="python",
        )
        node._screenshot_available = True  # type: ignore[attr-defined]
        node._capture_screenshot_to_file = lambda: (True, "C:/tmp/python_shot.png")  # type: ignore[attr-defined]
    else:
        node = LocalDesktopNode(
            capture_manager=_FakeCaptureManager(),
            action_enabled=False,
            backend="rust",
            rust_client=_FakeDesktopRustClient(),  # type: ignore[arg-type]
        )
    registry.register(node)
    tool = NodeInvokeTool(registry)

    process = psutil.Process()
    latencies: List[float] = []
    rss_samples: List[float] = []
    errors = 0

    for _ in range(iterations):
        started = time.perf_counter()
        result = await tool.execute(action="screen.screenshot", args={})
        latencies.append((time.perf_counter() - started) * 1000.0)
        rss_samples.append(float(process.memory_info().rss))
        if str(result).startswith("Error ["):
            errors += 1

    return {
        "iterations": iterations,
        "avg_ms": round(statistics.mean(latencies), 3) if latencies else 0.0,
        "p95_ms": round(_p95(latencies), 3),
        "error_rate": round(errors / iterations, 4) if iterations > 0 else 0.0,
        "rss_avg_mb": round((statistics.mean(rss_samples) / (1024 * 1024)) if rss_samples else 0.0, 3),
        "rss_peak_mb": round((max(rss_samples) / (1024 * 1024)) if rss_samples else 0.0, 3),
    }


async def _bench_exec_tool(mode: str, iterations: int) -> Dict[str, Any]:
    workspace = Path.cwd()
    if mode == "python":
        tool = ExecTool(workspace, shell_ops=None)
    else:
        shell_ops = RustShellOperations(_FakeShellRustClient())  # type: ignore[arg-type]
        tool = ExecTool(workspace, shell_ops=shell_ops)

    process = psutil.Process()
    latencies: List[float] = []
    rss_samples: List[float] = []
    errors = 0

    for _ in range(iterations):
        started = time.perf_counter()
        result = await tool.execute(command="echo bench", workdir=".", timeout=10)
        latencies.append((time.perf_counter() - started) * 1000.0)
        rss_samples.append(float(process.memory_info().rss))
        if str(result).startswith("Error ["):
            errors += 1

    return {
        "iterations": iterations,
        "avg_ms": round(statistics.mean(latencies), 3) if latencies else 0.0,
        "p95_ms": round(_p95(latencies), 3),
        "error_rate": round(errors / iterations, 4) if iterations > 0 else 0.0,
        "rss_avg_mb": round((statistics.mean(rss_samples) / (1024 * 1024)) if rss_samples else 0.0, 3),
        "rss_peak_mb": round((max(rss_samples) / (1024 * 1024)) if rss_samples else 0.0, 3),
    }


async def main() -> None:
    screenshot_iters = 40
    tool_iters = 60

    py_shot = await _bench_screenshot("python", screenshot_iters)
    rust_shot = await _bench_screenshot("rust", screenshot_iters)
    py_tool = await _bench_exec_tool("python", tool_iters)
    rust_tool = await _bench_exec_tool("rust", tool_iters)

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "benchmark": {
            "screenshot": {
                "python": py_shot,
                "rust": rust_shot,
            },
            "tool_exec": {
                "python": py_tool,
                "rust": rust_tool,
            },
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

