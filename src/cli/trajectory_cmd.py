"""CLI subcommand: ``gazer trajectory list|show|steps``.

This is a lightweight offline viewer for TrajectoryStore JSONL runs.
It does not require the Admin API to be running.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from agent.trajectory import TrajectoryStore


def register_parser(subparsers: argparse._SubParsersAction) -> None:
    traj_parser = subparsers.add_parser("trajectory", help="Inspect stored agent trajectories")
    traj_sub = traj_parser.add_subparsers(dest="traj_action")

    list_parser = traj_sub.add_parser("list", help="List recent trajectories")
    list_parser.add_argument("--limit", "-n", type=int, default=20, help="Max items to list (default: 20)")
    list_parser.add_argument("--session-key", type=str, default="", help="Filter by session key")

    show_parser = traj_sub.add_parser("show", help="Show one trajectory (meta + final)")
    show_parser.add_argument("run_id", help="Trajectory run id (e.g. traj_...)")

    steps_parser = traj_sub.add_parser("steps", help="Show normalized replay steps")
    steps_parser.add_argument("run_id", help="Trajectory run id (e.g. traj_...)")
    steps_parser.add_argument("--json", action="store_true", help="Output JSON instead of text")
    steps_parser.add_argument("--max", type=int, default=80, help="Max steps to show (default: 80)")

    traj_parser.set_defaults(func=_handle_trajectory)


def _handle_trajectory(args: argparse.Namespace) -> None:
    action = getattr(args, "traj_action", None)
    if not action:
        print("Usage: python -m cli trajectory {list|show|steps}", file=sys.stderr)
        return

    store = TrajectoryStore()
    if action == "list":
        _traj_list(store, args)
    elif action == "show":
        _traj_show(store, args)
    elif action == "steps":
        _traj_steps(store, args)


def _traj_list(store: TrajectoryStore, args: argparse.Namespace) -> None:
    limit = max(1, min(int(getattr(args, "limit", 20) or 20), 200))
    session_key = str(getattr(args, "session_key", "") or "").strip() or None
    items = store.list_recent(limit=limit, session_key=session_key)
    if not items:
        print("No trajectories found.")
        return
    for item in items:
        run_id = item.get("run_id")
        status = item.get("status", "running")
        ts = item.get("ts")
        preview = str(item.get("final_preview", "") or "").replace("\n", " ")[:120]
        print(f"{run_id}  status={status}  ts={ts}  {preview}")


def _traj_show(store: TrajectoryStore, args: argparse.Namespace) -> None:
    run_id = str(getattr(args, "run_id", "") or "").strip()
    payload = store.get_trajectory(run_id)
    if not payload:
        print(f"Trajectory not found: {run_id}", file=sys.stderr)
        sys.exit(1)
    meta = payload.get("meta") or {}
    final = payload.get("final") or {}
    out = {
        "run_id": run_id,
        "meta": meta,
        "final": final,
        "event_count": payload.get("event_count", 0),
    }
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))


def _normalize_steps(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize trajectory events into a simple replay timeline."""
    events = list(payload.get("events") or [])
    steps: List[Dict[str, Any]] = []
    tool_calls: Dict[str, Dict[str, Any]] = {}

    for evt in events:
        if not isinstance(evt, dict):
            continue
        action = str(evt.get("action", "") or "")
        stage = str(evt.get("stage", "") or "")
        pl = evt.get("payload") or {}
        if not isinstance(pl, dict):
            pl = {}

        if action == "llm_response":
            steps.append(
                {
                    "kind": "llm_response",
                    "stage": stage,
                    "iteration": pl.get("iteration"),
                    "error": pl.get("error"),
                    "finish_reason": pl.get("finish_reason"),
                    "has_tool_calls": pl.get("has_tool_calls"),
                    "request_id": pl.get("request_id"),
                    "model": pl.get("model"),
                }
            )
            continue

        if action == "tool_call":
            tool_call_id = str(pl.get("tool_call_id", "") or "")
            item = {
                "kind": "tool",
                "stage": stage or "act",
                "tool": pl.get("tool"),
                "tool_call_id": tool_call_id,
                "args_preview": pl.get("args_preview"),
                "args_hash": pl.get("args_hash"),
            }
            if tool_call_id:
                tool_calls[tool_call_id] = item
            steps.append(item)
            continue

        if action == "tool_result":
            tool_call_id = str(pl.get("tool_call_id", "") or "")
            target = tool_calls.get(tool_call_id)
            if target is None:
                target = {
                    "kind": "tool",
                    "stage": stage or "act",
                    "tool": pl.get("tool"),
                    "tool_call_id": tool_call_id,
                }
                steps.append(target)
            target["status"] = pl.get("status")
            target["result_preview"] = pl.get("result_preview")
            if pl.get("error_code"):
                target["error_code"] = pl.get("error_code")
            if pl.get("trace_id"):
                target["trace_id"] = pl.get("trace_id")
            if pl.get("error_hint"):
                target["error_hint"] = pl.get("error_hint")
            if pl.get("has_media"):
                target["has_media"] = True
            continue

    return steps


def _traj_steps(store: TrajectoryStore, args: argparse.Namespace) -> None:
    run_id = str(getattr(args, "run_id", "") or "").strip()
    payload = store.get_trajectory(run_id)
    if not payload:
        print(f"Trajectory not found: {run_id}", file=sys.stderr)
        sys.exit(1)
    steps = _normalize_steps(payload)
    max_steps = max(1, min(int(getattr(args, "max", 80) or 80), 500))
    steps = steps[:max_steps]

    if bool(getattr(args, "json", False)):
        print(json.dumps({"run_id": run_id, "steps": steps}, indent=2, ensure_ascii=False, default=str))
        return

    for step in steps:
        kind = step.get("kind")
        if kind == "llm_response":
            it = step.get("iteration")
            finish = step.get("finish_reason")
            tool_calls = step.get("has_tool_calls")
            err = step.get("error")
            print(f"[llm] iter={it} finish={finish} tools={tool_calls} error={err}")
            continue
        if kind == "tool":
            tool = step.get("tool")
            status = step.get("status", "")
            code = step.get("error_code", "")
            trace_id = step.get("trace_id", "")
            args_hash = step.get("args_hash", "")
            prefix = f"[tool] {tool} status={status} args_hash={args_hash}"
            if code:
                prefix += f" code={code}"
            if trace_id:
                prefix += f" trace_id={trace_id}"
            print(prefix)
            preview = str(step.get("result_preview", "") or "").strip()
            if preview:
                print(f"  {preview}")
            hint = str(step.get("error_hint", "") or "").strip()
            if hint:
                print(f"  hint: {hint}")

