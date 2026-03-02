"""CLI subcommand: ``gazer flow run|list|status``.

Allows running GazerFlow workflows directly from the terminal without
spinning up the full agent REPL.
"""

import asyncio
import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

from flow.engine import FlowEngine
from flow.state import StateStore
from tools.registry import ToolRegistry

logger = logging.getLogger("FlowCLI")


def register_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``flow`` subcommand."""
    flow_parser = subparsers.add_parser("flow", help="Manage and run GazerFlow workflows")
    flow_sub = flow_parser.add_subparsers(dest="flow_action")

    # --- flow list ---
    flow_sub.add_parser("list", help="List discovered workflows")

    # --- flow run ---
    run_parser = flow_sub.add_parser("run", help="Run a workflow")
    run_parser.add_argument("name", help="Flow name")
    run_parser.add_argument(
        "--arg", "-a", action="append", default=[],
        metavar="KEY=VALUE",
        help="Flow argument (repeatable). Example: --arg repo_path=. --arg days=3",
    )
    run_parser.add_argument(
        "--dir", "-d", action="append", default=[],
        help="Additional workflow search directory (repeatable)",
    )

    # --- flow status ---
    status_parser = flow_sub.add_parser("status", help="Show persisted state for a flow")
    status_parser.add_argument("name", help="Flow name")

    # --- flow resume ---
    resume_parser = flow_sub.add_parser("resume", help="Resume a flow past an approval gate")
    resume_parser.add_argument("token", help="Resume token from a previous needs_approval result")
    resume_parser.add_argument(
        "--dir", "-d", action="append", default=[],
        help="Additional workflow search directory",
    )

    flow_parser.set_defaults(func=_handle_flow)


def _parse_args(raw_args: List[str]) -> dict:
    """Parse ``--arg KEY=VALUE`` pairs into a dict."""
    result = {}
    for item in raw_args:
        if "=" not in item:
            print(f"Warning: ignoring malformed --arg '{item}' (expected KEY=VALUE)", file=sys.stderr)
            continue
        key, _, value = item.partition("=")
        # Try to parse as JSON for typed values (int, bool, list)
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            pass  # keep as string
        result[key.strip()] = value
    return result


def _build_engine(extra_dirs: Optional[List[str]] = None) -> FlowEngine:
    """Build a FlowEngine with a minimal (empty) tool registry.

    In CLI mode, actual tool execution requires the full agent setup.
    For listing/status this is fine. For ``run``, tools registered via
    plugins would be needed — a future improvement.
    """
    flow_dirs = [Path("workflows"), Path.home() / ".gazer" / "workflows"]
    if extra_dirs:
        flow_dirs.extend(Path(d) for d in extra_dirs)

    # Minimal tool registry (tools can be added later via plugin loading)
    registry = ToolRegistry()

    return FlowEngine(
        tool_registry=registry,
        state_store=StateStore(),
        flow_dirs=flow_dirs,
    )


def _handle_flow(args: argparse.Namespace) -> None:
    """Dispatch flow subcommand."""
    action = getattr(args, "flow_action", None)
    if not action:
        print("Usage: python -m cli flow {list|run|status|resume}")
        print("Run 'python -m cli flow --help' for details.")
        return

    if action == "list":
        _flow_list(args)
    elif action == "run":
        _flow_run(args)
    elif action == "status":
        _flow_status(args)
    elif action == "resume":
        _flow_resume(args)


def _flow_list(args: argparse.Namespace) -> None:
    engine = _build_engine()
    flows = engine.list_flows()
    if not flows:
        print("No workflows found.")
        return
    print(f"Discovered {len(flows)} workflow(s):\n")
    for f in flows:
        print(f"  {f['name']}")
        if f.get("description"):
            print(f"    {f['description']}")
        if f.get("args"):
            for k, v in f["args"].items():
                default = v.get("default", "")
                print(f"    --arg {k}={default}  ({v.get('type', 'string')})")
        print()


def _flow_run(args: argparse.Namespace) -> None:
    extra_dirs = getattr(args, "dir", [])
    engine = _build_engine(extra_dirs)
    flow_args = _parse_args(args.arg)

    async def _run():
        result = await engine.run(args.name, flow_args)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
        if result.status == "needs_approval":
            print(f"\nFlow paused at approval gate '{result.pending_step}'.")
            print(f"Prompt: {result.prompt}")
            print(f"\nTo resume:\n  python -m cli flow resume \"{result.resume_token}\"")
        elif result.status == "error":
            sys.exit(1)

    asyncio.run(_run())


def _flow_status(args: argparse.Namespace) -> None:
    engine = _build_engine()
    info = engine.status(args.name)
    print(json.dumps(info, indent=2, ensure_ascii=False, default=str))


def _flow_resume(args: argparse.Namespace) -> None:
    extra_dirs = getattr(args, "dir", [])
    engine = _build_engine(extra_dirs)

    async def _resume():
        result = await engine.resume(args.token)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
        if result.status == "error":
            sys.exit(1)

    asyncio.run(_resume())
