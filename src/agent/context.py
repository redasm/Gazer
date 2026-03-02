"""Context builder for assembling agent prompts."""

import base64
import mimetypes
from pathlib import Path
from typing import Any, List, Dict, Optional

from agent.agents_md import resolve_agents_overlay

class ContextBuilder:
    """
    Builds the context (system prompt + messages) for the agent.
    
    Assembles bootstrap files, memory, skills, and conversation history
    into a coherent prompt for the LLM.
    """
    
    # Bootstrap files loaded from assets/ directory
    BOOTSTRAP_FILES = ["AGENTS.md", "USER.md", "TOOLS.md", "IDENTITY.md"]
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.skills = None
        self._agents_target_dir: Path = workspace
        self._agents_debug: List[Dict[str, Any]] = []
        self._skill_priority: List[str] = []
        self._agents_allowed_tools: List[str] = []
        self._agents_deny_tools: List[str] = []
        self._agents_routing_hints: List[str] = []
    
    def build_system_prompt(self, skill_names: Optional[List[str]] = None) -> str:
        """
        Build the system prompt.
        For Gazer, we will override this in the Adapter to inject Gazer's memory.
        """
        parts = []
        
        # Core identity
        parts.append(self._get_identity())

        # Workspace AGENTS.md overlay (directory chain with child override rules).
        agents_overlay = self._load_workspace_agents_overlay()
        if agents_overlay:
            parts.append(agents_overlay)
        
        # Load bootstrap files (TOOLS.md, SOUL.md, etc.)
        bootstrap_content = self._load_bootstrap_files()
        if bootstrap_content:
            parts.append(bootstrap_content)

        if skill_names:
            names = [str(name).strip() for name in skill_names if str(name).strip()]
            if names:
                parts.append("## Requested Skills\n" + "\n".join([f"- {name}" for name in names]))

        return "\n\n---\n\n".join(parts)

    def set_agents_target_dir(self, target_dir: Optional[Path]) -> None:
        """Set directory context used for hierarchical AGENTS.md resolution."""
        if target_dir is None:
            self._agents_target_dir = self.workspace
            return
        try:
            resolved = target_dir.resolve()
        except OSError:
            self._agents_target_dir = self.workspace
            return
        self._agents_target_dir = resolved

    def get_skill_priority(self) -> List[str]:
        return list(self._skill_priority)

    def get_agents_debug(self) -> List[Dict[str, Any]]:
        return list(self._agents_debug)

    def get_agents_tool_policy_overlay(self) -> Dict[str, List[str]]:
        # Refresh from current target dir so per-turn policy sees latest AGENTS.md state.
        self._load_workspace_agents_overlay()
        return {
            "allowed_tools": list(self._agents_allowed_tools),
            "deny_tools": list(self._agents_deny_tools),
        }

    def get_agents_routing_hints(self) -> List[str]:
        self._load_workspace_agents_overlay()
        return list(self._agents_routing_hints)
    
    # Maximum characters per bootstrap file (inspired by OpenClaw's bootstrapMaxChars)
    BOOTSTRAP_MAX_CHARS = 20000

    def _load_workspace_agents_overlay(self) -> str:
        payload = resolve_agents_overlay(self.workspace, self._agents_target_dir)
        self._agents_debug = list(payload.get("debug", [])) if isinstance(payload, dict) else []
        self._skill_priority = (
            [str(item).strip() for item in payload.get("skill_priority", []) if str(item).strip()]
            if isinstance(payload, dict)
            else []
        )
        self._agents_allowed_tools = (
            [str(item).strip() for item in payload.get("allowed_tools", []) if str(item).strip()]
            if isinstance(payload, dict)
            else []
        )
        self._agents_deny_tools = (
            [str(item).strip() for item in payload.get("deny_tools", []) if str(item).strip()]
            if isinstance(payload, dict)
            else []
        )
        self._agents_routing_hints = (
            [str(item).strip() for item in payload.get("routing_hints", []) if str(item).strip()]
            if isinstance(payload, dict)
            else []
        )
        text = str(payload.get("combined_text", "") if isinstance(payload, dict) else "").strip()
        if len(text) > self.BOOTSTRAP_MAX_CHARS:
            return text[: self.BOOTSTRAP_MAX_CHARS] + (
                f"\n\n[...truncated, {len(text) - self.BOOTSTRAP_MAX_CHARS} chars omitted...]"
            )
        return text
    
    def _load_bootstrap_files(self) -> str:
        """Load content from bootstrap files in assets/ directory.
        
        Files are loaded in order and truncated if they exceed BOOTSTRAP_MAX_CHARS.
        Empty files are skipped.
        """
        # Find the assets directory (relative to workspace or project root)
        possible_paths = [
            self.workspace / "assets",
            Path(__file__).parent.parent.parent / "assets",  # project root
        ]
        
        assets_dir = None
        for p in possible_paths:
            if p.is_dir():
                assets_dir = p
                break
        
        if not assets_dir:
            return ""
        
        contents = []
        for filename in self.BOOTSTRAP_FILES:
            filepath = assets_dir / filename
            if filepath.is_file():
                try:
                    text = filepath.read_text(encoding="utf-8").strip()
                    if not text:
                        continue
                    # Truncate large files to avoid excessive token usage
                    if len(text) > self.BOOTSTRAP_MAX_CHARS:
                        text = text[:self.BOOTSTRAP_MAX_CHARS] + f"\n\n[...truncated, {len(text) - self.BOOTSTRAP_MAX_CHARS} chars omitted...]"
                    contents.append(text)
                except (OSError, UnicodeDecodeError):
                    pass
        
        return "\n\n".join(contents)
    
    def _get_identity(self) -> str:
        """Get the core identity section."""
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        workspace_path = str(self.workspace.expanduser().resolve())
        
        return f"""# Gazer

You are Gazer, an embodied AI companion living on the user's desktop.
You have access to the user's screen, audio, and hardware peripherals.
Never reveal internal implementation details, module names, or system architecture to the user.

## Current Time
{now}

## Workspace
Your workspace is at: {workspace_path}

IMPORTANT: When responding to direct questions or conversations, reply directly with your text response.
Only use tools when necessary to perform an action.

语言规则：默认跟随用户输入语言回复；若无法判断用户语言，默认使用中文。

CRITICAL: You MUST actually call tools to perform actions. NEVER claim to have performed an action (like taking screenshots, sending files, running commands) without actually calling the corresponding tool. If you describe doing something, you MUST have called the tool first. Saying "I've taken a screenshot" without calling `node_invoke` with `action=screen.screenshot` is FORBIDDEN.
"""
    
    def build_messages(
        self,
        history: List[Dict[str, Any]],
        current_message: str,
        skill_names: Optional[List[str]] = None,
        media: Optional[List[str]] = None,
        channel: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Build the complete message list for an LLM call.
        """
        messages = []

        # System prompt
        system_prompt = self.build_system_prompt(skill_names)
        if channel and chat_id:
            system_prompt += f"\n\n## Current Session\nChannel: {channel}\nChat ID: {chat_id}"
        messages.append({"role": "system", "content": system_prompt})

        # History
        messages.extend(history)

        # Current message (with optional image attachments)
        user_content = self._build_user_content(current_message, media)
        messages.append({"role": "user", "content": user_content})

        return messages

    def _build_user_content(self, text: str, media: Optional[List[str]]) -> Any:
        """Build user message content with optional images.

        Each item in *media* can be:
        - A local file path  → base64-encoded inline
        - An ``http(s)://`` URL → passed directly as ``image_url``
        """
        if not media:
            return text

        images: list[dict] = []
        for item in media:
            # URL-based media (e.g. from a CDN or screenshot URL)
            if item.startswith(("http://", "https://", "data:")):
                images.append({"type": "image_url", "image_url": {"url": item}})
                continue

            # Local file path → read + base64
            p = Path(item)
            mime, _ = mimetypes.guess_type(item)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            try:
                if p.stat().st_size > 20 * 1024 * 1024:
                    continue
            except OSError:
                continue
            b64 = base64.b64encode(p.read_bytes()).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

        if not images:
            return text
        return [{"type": "text", "text": text}] + images
    
    def add_tool_result(
        self,
        messages: List[Dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str
    ) -> List[Dict[str, Any]]:
        """Add a tool result to the message list."""
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result
        })
        return messages
    
    def add_assistant_message(
        self,
        messages: List[Dict[str, Any]],
        content: Optional[str],
        tool_calls: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """Add an assistant message to the message list."""
        msg: Dict[str, Any] = {"role": "assistant", "content": content or ""}
        
        if tool_calls:
            msg["tool_calls"] = tool_calls
        
        messages.append(msg)
        return messages
