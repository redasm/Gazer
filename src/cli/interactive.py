"""Minimal interactive CLI for Gazer.

Provides a REPL with:
- Streaming agent responses via GazerAgent.process_message()
- Built-in slash commands (/plan, /commit, /run-tests, /status, /help, /quit)
- Async event loop integration

Usage:
    python -m cli.interactive [--workspace PATH]
"""

import asyncio
import argparse
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any, Dict, Callable, Awaitable, Optional

from agent.adapter import GazerAgent
from tools.coding import (
    ExecTool, ReadFileTool, WriteFileTool, EditFileTool,
    ListDirTool, FindFilesTool, GitStatusTool, GitDiffTool,
    GitCommitTool, GitLogTool, GitPushTool, GitBranchTool,
    GrepTool, ReadSkillTool,
)
from tools.web_tools import WebSearchTool, WebFetchTool, WebReportTool
from tools.system_tools import GetTimeTool
from skills.loader import SkillLoader
from scheduler.cron import CronScheduler
from tools.cron_tool import CronTool
from skills.registry_client import SkillRegistryClient, SkillSearchTool, SkillInstallTool
from memory import MemoryManager
from memory.openviking_bootstrap import ensure_openviking_ready
from runtime.config_manager import config

logger = logging.getLogger("GazerCLI")

# ANSI colours (degrade gracefully on dumb terminals)
_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
_CYAN = "\033[36m" if _USE_COLOR else ""
_GREEN = "\033[32m" if _USE_COLOR else ""
_YELLOW = "\033[33m" if _USE_COLOR else ""
_DIM = "\033[2m" if _USE_COLOR else ""
_BOLD = "\033[1m" if _USE_COLOR else ""
_RESET = "\033[0m" if _USE_COLOR else ""

BANNER = f"""{_BOLD}{_CYAN}
  ╔═══════════════════════════════════╗
  ║          Gazer CLI  v0.1          ║
  ║  Type /help for commands, /quit   ║
  ╚═══════════════════════════════════╝{_RESET}
"""


class InteractiveCLI:
    """REPL that wraps a lightweight GazerAgent for terminal use."""

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        ensure_openviking_ready(config)
        self.memory_manager = MemoryManager()
        self.agent = GazerAgent(self.workspace, self.memory_manager)
        self._running = False

        self._cron_scheduler: Optional[CronScheduler] = None

        self._flow_engine = None  # Initialized in _setup()

        # Slash command registry: name -> async handler
        self._commands: Dict[str, Callable[..., Awaitable[None]]] = {
            "help": self._cmd_help,
            "quit": self._cmd_quit,
            "exit": self._cmd_quit,
            "status": self._cmd_status,
            "plan": self._cmd_plan,
            "commit": self._cmd_commit,
            "run-tests": self._cmd_run_tests,
            "tools": self._cmd_tools,
            "branches": self._cmd_branches,
            "new": self._cmd_new_session,
            "reset": self._cmd_new_session,
            "cron": self._cmd_cron,
            "doctor": self._cmd_doctor,
            "canvas": self._cmd_canvas,
            "flow": self._cmd_flow,
        }

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    async def _setup(self) -> None:
        """Register tools and skills (headless subset -- no hardware/desktop)."""
        ws = self.workspace

        # Coding tools
        for tool in [
            ExecTool(ws),
            ReadFileTool(ws),
            WriteFileTool(ws),
            EditFileTool(ws),
            ListDirTool(ws),
            FindFilesTool(ws),
            GitStatusTool(ws),
            GitDiffTool(ws),
            GitCommitTool(ws),
            GitLogTool(ws),
            GitPushTool(ws),
            GitBranchTool(ws),
            GrepTool(ws),
        ]:
            self.agent.register_tool(tool)

        # Web & system tools
        self.agent.register_tool(WebSearchTool())
        self.agent.register_tool(WebFetchTool())
        self.agent.register_tool(WebReportTool(memory_manager=self.memory_manager))
        self.agent.register_tool(GetTimeTool())

        # Read-skill tool
        read_skill = ReadSkillTool()
        self.agent.register_tool(read_skill)

        # Cron scheduler + tool (headless mode)
        if config.get("scheduler.cron_enabled", True):
            self._cron_scheduler = CronScheduler(
                run_callback=self._run_cron_job,
            )
            self._cron_scheduler.load()
            self.agent.register_tool(CronTool(self._cron_scheduler))

        # Skill registry tools
        registry_url = config.get("skills.registry_url", "")
        skill_client = SkillRegistryClient(registry_url)
        self.agent.register_tool(SkillSearchTool(skill_client))
        self.agent.register_tool(SkillInstallTool(skill_client, ws / "skills"))

        # Skills loader
        skills_dirs = [
            ws / "skills",
            Path.home() / ".gazer" / "skills",
            Path(__file__).resolve().parent.parent / "skills",
        ]
        loader = SkillLoader(skills_dirs)
        loader.discover()
        self.agent.set_skill_loader(loader)
        read_skill.set_skill_loader(loader)

        # GazerFlow — workflow engine
        from flow.engine import FlowEngine
        from flow.tool import FlowRunTool

        flow_dirs = [
            ws / "workflows",
            Path.home() / ".gazer" / "workflows",
        ]
        self._flow_engine = FlowEngine(
            tool_registry=self.agent.loop.tools,
            llm_provider=self.agent.provider,
            flow_dirs=flow_dirs,
        )
        self.agent.register_tool(FlowRunTool(self._flow_engine))

        # Tool security policy
        denylist = config.get("security.tool_denylist", [])
        if denylist:
            self.agent.loop.tools.set_denylist(denylist)

        flow_count = len(self._flow_engine.list_flows())
        logger.info(
            f"CLI registered {len(self.agent.loop.tools)} tools, "
            f"{len(loader.skills)} skills, {flow_count} workflow(s)."
        )

    async def _run_cron_job(self, job) -> Optional[str]:
        """Cron callback for CLI mode."""
        try:
            return await self.agent.process_message(content=job.message, sender="cron")
        except Exception as exc:
            return f"Error: {exc}"

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    async def _cmd_help(self, _args: str) -> None:
        print(f"""
{_BOLD}Slash commands:{_RESET}
  {_GREEN}/help{_RESET}            Show this message
  {_GREEN}/status{_RESET}          Git status of the workspace
  {_GREEN}/branches{_RESET}        List git branches
  {_GREEN}/plan{_RESET} <task>      Ask the agent to produce a plan
  {_GREEN}/commit{_RESET} <msg>     Stage all & commit with message
  {_GREEN}/run-tests{_RESET} [cmd]  Run test suite (default: auto-detect)
  {_GREEN}/tools{_RESET}           List registered tools
  {_GREEN}/new{_RESET}             Start a new session (clear history)
  {_GREEN}/reset{_RESET}           Alias for /new
  {_GREEN}/cron{_RESET} [list]     List/manage cron jobs
  {_GREEN}/doctor{_RESET}          Run system diagnostics
  {_GREEN}/canvas{_RESET}          Show canvas panel summary
  {_GREEN}/flow{_RESET} [list|run]  Manage GazerFlow workflows
  {_GREEN}/quit{_RESET}            Exit the CLI
""")

    async def _cmd_quit(self, _args: str) -> None:
        self._running = False
        print(f"{_DIM}Goodbye!{_RESET}")

    async def _cmd_status(self, _args: str) -> None:
        # Show model, usage, and git status
        usage = self.agent.loop.usage.summary()
        print(f"{_BOLD}Model:{_RESET} {self.agent.loop.model}")
        print(
            f"{_BOLD}Usage:{_RESET} {usage['total_tokens']} tokens "
            f"({usage['prompt_tokens']} prompt + {usage['completion_tokens']} completion) "
            f"across {usage['requests']} request(s)"
        )
        result = await self.agent.loop.tools.execute("git_status", {})
        print(f"{_BOLD}Git:{_RESET}\n{result}")

    async def _cmd_branches(self, _args: str) -> None:
        result = await self.agent.loop.tools.execute("git_branch", {})
        print(result)

    async def _cmd_plan(self, args: str) -> None:
        if not args.strip():
            print(f"{_YELLOW}Usage: /plan <task description>{_RESET}")
            return
        prompt = (
            f"Please produce a detailed step-by-step plan for the following task. "
            f"Do NOT execute anything yet, just plan.\n\nTask: {args}"
        )
        await self._send_and_print(prompt)

    async def _cmd_commit(self, args: str) -> None:
        msg = args.strip()
        if not msg:
            print(f"{_YELLOW}Usage: /commit <message>{_RESET}")
            return
        result = await self.agent.loop.tools.execute("git_commit", {"message": msg})
        print(result)

    async def _cmd_run_tests(self, args: str) -> None:
        cmd = args.strip()
        if not cmd:
            # Auto-detect
            ws = self.workspace
            if (ws / "pytest.ini").exists() or (ws / "pyproject.toml").exists():
                cmd = "python -m pytest --tb=short -q"
            elif (ws / "package.json").exists():
                cmd = "npm test"
            elif (ws / "Makefile").exists():
                cmd = "make test"
            else:
                cmd = "python -m pytest --tb=short -q"
        print(f"{_DIM}Running: {cmd}{_RESET}")
        result = await self.agent.loop.tools.execute(
            "exec", {"command": cmd, "timeout": 120}
        )
        print(result)

    async def _cmd_tools(self, _args: str) -> None:
        names = self.agent.loop.tools.tool_names
        print(f"{_BOLD}Registered tools ({len(names)}):{_RESET}")
        for n in sorted(names):
            tool = self.agent.loop.tools.get(n)
            owner = "owner_only" if (tool and tool.owner_only) else "public"
            desc = (tool.description[:60] + "...") if tool and len(tool.description) > 60 else (tool.description if tool else "")
            print(f"  {_GREEN}{n:20s}{_RESET} [{owner:10s}] {desc}")

    async def _cmd_new_session(self, _args: str) -> None:
        """Reset the current session (clear conversation history)."""
        session_key = "gazer:main"
        self.agent.loop.reset_session(session_key)
        print(f"{_CYAN}Session reset. Starting fresh.{_RESET}")

    async def _cmd_cron(self, args: str) -> None:
        """List cron jobs (pass args to the cron tool for add/remove)."""
        if not self._cron_scheduler:
            print(f"{_YELLOW}Cron scheduler is disabled.{_RESET}")
            return
        jobs = self._cron_scheduler.list_jobs()
        if not jobs:
            print(f"{_DIM}No cron jobs configured.{_RESET}")
            return
        print(f"{_BOLD}Cron jobs ({len(jobs)}):{_RESET}")
        for j in jobs:
            status = f"{_GREEN}enabled{_RESET}" if j.enabled else f"{_YELLOW}disabled{_RESET}"
            print(f"  {j.id}: {j.name} [{j.cron_expr}] ({status})")

    async def _cmd_canvas(self, _args: str) -> None:
        """Show current canvas panel summary."""
        try:
            from tools.canvas import CanvasState
            # Try to get canvas state from a registered canvas_snapshot tool
            tool = self.agent.loop.tools.get("canvas_snapshot")
            if tool is None:
                print(f"{_DIM}Canvas tools not registered (canvas disabled or not in full brain mode).{_RESET}")
                return
            result = await tool.execute()
            print(f"{_BOLD}Canvas:{_RESET}\n{result}")
        except Exception as exc:
            print(f"{_YELLOW}Canvas error: {exc}{_RESET}")

    async def _cmd_flow(self, args: str) -> None:
        """Manage GazerFlow workflows: /flow list, /flow run <name> [key=val ...]."""
        if not self._flow_engine:
            print(f"{_YELLOW}Flow engine not initialized.{_RESET}")
            return

        parts = args.strip().split(None, 1)
        action = parts[0] if parts else "list"
        rest = parts[1] if len(parts) > 1 else ""

        if action == "list":
            flows = self._flow_engine.list_flows()
            if not flows:
                print(f"{_DIM}No workflows found.{_RESET}")
                return
            print(f"{_BOLD}Workflows ({len(flows)}):{_RESET}")
            for f in flows:
                desc = f" — {f['description'][:60]}" if f.get('description') else ""
                print(f"  {_GREEN}{f['name']}{_RESET}{desc}")

        elif action == "run":
            if not rest:
                print(f"{_YELLOW}Usage: /flow run <name> [key=val ...]{_RESET}")
                return
            run_parts = rest.split()
            flow_name = run_parts[0]
            flow_args = {}
            for kv in run_parts[1:]:
                if "=" in kv:
                    k, _, v = kv.partition("=")
                    import json as _json
                    try:
                        v = _json.loads(v)
                    except (ValueError, _json.JSONDecodeError):
                        pass
                    flow_args[k.strip()] = v
            print(f"{_DIM}Running workflow '{flow_name}'...{_RESET}")
            result = await self._flow_engine.run(flow_name, flow_args)
            import json as _json
            print(_json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
            if result.status == "needs_approval":
                print(f"\n{_YELLOW}Paused at approval gate '{result.pending_step}'.{_RESET}")
                print(f"Use: /flow resume <token>")

        elif action == "resume":
            if not rest:
                print(f"{_YELLOW}Usage: /flow resume <token>{_RESET}")
                return
            result = await self._flow_engine.resume(rest.strip())
            import json as _json
            print(_json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))

        elif action == "status":
            if not rest:
                print(f"{_YELLOW}Usage: /flow status <name>{_RESET}")
                return
            import json as _json
            info = self._flow_engine.status(rest.strip())
            print(_json.dumps(info, indent=2, ensure_ascii=False, default=str))

        else:
            print(f"{_YELLOW}Unknown flow action: {action}. Use: list, run, resume, status{_RESET}")

    async def _cmd_doctor(self, _args: str) -> None:
        """Run local diagnostic checks."""
        print(f"{_BOLD}Running diagnostics...{_RESET}")
        issues = 0

        # 1. LLM connectivity
        try:
            resp = await self.agent.provider.chat(
                messages=[{"role": "user", "content": "Say OK"}],
                tools=[], max_tokens=10,
            )
            if resp.error:
                print(f"  {_YELLOW}LLM:{_RESET} error — {resp.content[:80]}")
                issues += 1
            else:
                print(f"  {_GREEN}LLM:{_RESET} OK ({self.agent.loop.model})")
        except Exception as exc:
            print(f"  {_YELLOW}LLM:{_RESET} {exc}")
            issues += 1

        # 2. Tools
        print(f"  {_GREEN}Tools:{_RESET} {len(self.agent.loop.tools)} registered")

        # 3. Session store
        try:
            sessions = self.agent.loop.session_store.list_sessions()
            print(f"  {_GREEN}Sessions:{_RESET} {len(sessions)} stored")
        except Exception:
            print(f"  {_YELLOW}Sessions:{_RESET} error reading store")
            issues += 1

        # 4. Disk space
        try:
            import shutil
            total, used, free = shutil.disk_usage(self.workspace)
            pct = used / total * 100
            status = _GREEN if pct < 90 else _YELLOW
            print(f"  {status}Disk:{_RESET} {pct:.0f}% used ({free // (1024**3)} GB free)")
        except Exception:
            pass

        # 5. Cron
        if self._cron_scheduler:
            jobs = self._cron_scheduler.list_jobs()
            print(f"  {_GREEN}Cron:{_RESET} {len(jobs)} jobs")
        else:
            print(f"  {_DIM}Cron:{_RESET} disabled")

        overall = f"{_GREEN}healthy{_RESET}" if issues == 0 else f"{_YELLOW}{issues} issue(s){_RESET}"
        print(f"\n  {_BOLD}Overall:{_RESET} {overall}")

    # ------------------------------------------------------------------
    # Agent interaction
    # ------------------------------------------------------------------

    async def _send_and_print(self, content: str) -> None:
        """Send a message and stream the response. Ctrl+C cancels."""
        print(f"{_DIM}Thinking...{_RESET}", end="", flush=True)
        first_chunk = True
        try:
            async for chunk in self.agent.stream_response(content):
                if first_chunk:
                    print(f"\r{' ' * 20}\r{_CYAN}Gazer:{_RESET} ", end="", flush=True)
                    first_chunk = False
                print(chunk, end="", flush=True)
            if first_chunk:
                print(f"\r{' ' * 20}\r{_CYAN}Gazer:{_RESET} (no response)")
            else:
                print()
        except KeyboardInterrupt:
            # Ctrl+C during streaming → cancel the agent's current work
            self.agent.loop.cancel_current()
            print(f"\n{_YELLOW}(cancelled){_RESET}")
        except Exception as e:
            print(f"\r{_YELLOW}Error: {e}{_RESET}")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start the REPL."""
        print(BANNER)
        await self._setup()

        # Start agent loop in background
        agent_task = asyncio.create_task(self.agent.start())

        self._running = True
        print(f"{_DIM}Workspace: {self.workspace}{_RESET}")
        print(f"{_DIM}Tools: {len(self.agent.loop.tools)} | "
              f"Model: {self.agent.loop.model}{_RESET}\n")

        try:
            while self._running:
                try:
                    line = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: input(f"{_GREEN}You:{_RESET} ")
                    )
                except (EOFError, KeyboardInterrupt):
                    print()
                    break

                line = line.strip()
                if not line:
                    continue

                # Slash command dispatch
                if line.startswith("/"):
                    parts = line[1:].split(None, 1)
                    cmd_name = parts[0].lower()
                    cmd_args = parts[1] if len(parts) > 1 else ""
                    handler = self._commands.get(cmd_name)
                    if handler:
                        await handler(cmd_args)
                    else:
                        print(f"{_YELLOW}Unknown command: /{cmd_name}. Type /help for available commands.{_RESET}")
                    continue

                # Regular message -> send to agent
                await self._send_and_print(line)

        finally:
            self.agent.stop()
            agent_task.cancel()
            try:
                await agent_task
            except asyncio.CancelledError:
                pass


def main():
    parser = argparse.ArgumentParser(description="Gazer Interactive CLI")
    parser.add_argument(
        "--workspace", "-w",
        type=str,
        default=os.getcwd(),
        help="Workspace directory (defaults to current dir).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    cli = InteractiveCLI(Path(args.workspace))

    try:
        asyncio.run(cli.run())
    except KeyboardInterrupt:
        print("\nGazer CLI interrupted.")


if __name__ == "__main__":
    main()
