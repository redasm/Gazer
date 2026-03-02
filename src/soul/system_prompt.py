import os
import datetime
import logging
from typing import List, Dict, Optional, Any

logger = logging.getLogger("GazerSystemPrompt")


def _section(title: str, body: List[str]) -> List[str]:
    """Build a markdown section with a stable layout."""
    lines: List[str] = [f"## {title}"]
    lines.extend(body)
    lines.append("")
    return lines


def _read_asset(name: str) -> str:
    """Read an MD file from assets/ directory"""
    # Assuming code is running from project root or soul/..
    # Adjust path logic as needed. Here we assume we can find 'assets' in CWD or parent.
    paths = [
        os.path.join("assets", name),
        os.path.join("..", "assets", name),
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception:
                pass
    logger.warning(f"Asset file not found: {name} (searched {paths})")
    return ""


def _format_runtime_info(runtime_info: Optional[Dict[str, Any]]) -> List[str]:
    if not runtime_info:
        return []
    lines: List[str] = []
    for key, value in runtime_info.items():
        lines.append(f"- {key}: {value}")
    return lines


def _format_tooling(tool_summaries: Optional[Dict[str, str]], tools_content: str) -> List[str]:
    lines: List[str] = [
        "You can invoke tools to observe, verify, and act. Follow this protocol:",
        "- Prefer tool calls for uncertain or high-impact facts.",
        "- Never fabricate tool outputs; report tool failures explicitly.",
        "- For destructive or privileged actions, require explicit user confirmation.",
        "- Keep each tool call targeted and explain the outcome succinctly.",
    ]
    if tool_summaries:
        lines.append("")
        lines.append("Registered tools:")
        for name, desc in sorted(tool_summaries.items()):
            lines.append(f"- `{name}`: {desc}")
    if tools_content:
        lines.append("")
        lines.append("Tool usage reference:")
        lines.append(tools_content)
    return lines


def _format_context_files(context_files: Optional[List[Dict[str, str]]]) -> List[str]:
    if not context_files:
        return []
    lines: List[str] = []
    for context_file in context_files:
        path = context_file.get("path", "Unknown")
        content = context_file.get("content", "")
        lines.append(f"### {path}")
        lines.append(content)
        lines.append("")
    return lines


def build_agent_system_prompt(
    workspace_dir: str,
    tool_summaries: Optional[Dict[str, str]] = None,
    skill_instructions: Optional[str] = None,
    context_files: Optional[List[Dict[str, str]]] = None,
    runtime_info: Optional[Dict] = None
) -> str:
    """
    Dynamically build the System Prompt.
    Combines: SOUL, AGENTS, TOOLS, Skills, and Runtime context.
    """
    
    # 1. Load assets
    soul_content = _read_asset("SOUL.md")
    agents_content = _read_asset("AGENTS.md")
    tools_content = _read_asset("TOOLS.md")

    lines: List[str] = []

    # 2. Identity & mission
    identity_body = [
        "You are Gazer, a production-grade AI assistant that should be accurate, reliable, and safe.",
        "Primary objective: help the user complete tasks with verifiable, high-signal outputs.",
        "",
        "Persona:",
        soul_content if soul_content else "You are calm, direct, and collaborative.",
    ]
    lines.extend(_section("Identity & Mission", identity_body))

    # 3. Operating rules (local project policy)
    if agents_content:
        lines.extend(_section("Operating Rules", [agents_content]))

    # 4. Execution loop contract (OpenClaw-style)
    execution_body = [
        "Use an Observe -> Plan -> Act -> Verify loop:",
        "- Observe: collect facts from user input, memory, and tools.",
        "- Plan: choose the smallest safe next step.",
        "- Act: execute with clear intent and minimal side effects.",
        "- Verify: check outcomes and report concrete status.",
    ]
    lines.extend(_section("Execution Loop", execution_body))

    # 5. Runtime context
    runtime_body = [
        f"- workspace: {workspace_dir}",
        f"- current_time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    runtime_body.extend(_format_runtime_info(runtime_info))
    lines.extend(_section("Runtime Context", runtime_body))

    # 6. Tooling protocol
    lines.extend(_section("Tooling Protocol", _format_tooling(tool_summaries, tools_content)))

    # 7. Skills (dynamic)
    if skill_instructions:
        lines.extend(_section("Available Skills", [skill_instructions]))

    # 8. Additional context (injected files)
    if context_files:
        lines.extend(_section("Project Context", _format_context_files(context_files)))

    # 9. Output contract
    output_body = [
        "- Do not expose hidden chain-of-thought.",
        "- Provide concise, actionable answers in Markdown.",
        "- Default to the user's language from the latest user message (e.g., Chinese input -> Chinese reply).",
        "- Switch language only when the user explicitly requests a different language.",
        "- When uncertain, state uncertainty and propose the next verification step.",
        "- If a request conflicts with safety policy, refuse briefly and offer a safe alternative.",
    ]
    lines.extend(_section("Output Contract", output_body))

    return "\n".join(lines)
