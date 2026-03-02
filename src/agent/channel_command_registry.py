"""Unified channel command parser + registry for / and + commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Set


CHANNEL_COMMAND_PREFIXES: Set[str] = {"/", "+"}

CommandHandler = Callable[[List[str], Any], str]
MutatingRule = bool | Callable[[List[str]], bool]


def parse_channel_command(
    content: str,
    *,
    prefixes: Optional[Sequence[str]] = None,
) -> Optional[tuple[str, List[str]]]:
    raw = str(content or "").strip()
    allowed_prefixes = set(prefixes or CHANNEL_COMMAND_PREFIXES)
    if not raw or raw[0] not in allowed_prefixes:
        return None
    body = raw[1:].strip()
    if not body:
        return None
    parts = body.split()
    if not parts:
        return None
    command = parts[0].strip().lower()
    # Telegram group commands can look like /model@botname.
    if "@" in command:
        command = command.split("@", 1)[0].strip()
    if not command:
        return None
    return command, [str(item) for item in parts[1:]]


@dataclass
class RegisteredChannelCommand:
    name: str
    handler: CommandHandler
    mutating: MutatingRule = False


class ChannelCommandRegistry:
    """Registry-backed command dispatch shared by all channels."""

    def __init__(self, *, prefixes: Optional[Sequence[str]] = None) -> None:
        self.prefixes = set(prefixes or CHANNEL_COMMAND_PREFIXES)
        self._commands: Dict[str, RegisteredChannelCommand] = {}
        self._alias_map: Dict[str, str] = {}

    def register(
        self,
        name: str,
        handler: CommandHandler,
        *,
        aliases: Optional[Sequence[str]] = None,
        mutating: MutatingRule = False,
    ) -> None:
        clean = str(name or "").strip().lower()
        if not clean:
            raise ValueError("command name is required")
        entry = RegisteredChannelCommand(name=clean, handler=handler, mutating=mutating)
        self._commands[clean] = entry
        self._alias_map[clean] = clean
        for alias in aliases or []:
            alias_clean = str(alias or "").strip().lower()
            if alias_clean:
                self._alias_map[alias_clean] = clean

    def parse(self, content: str) -> Optional[tuple[str, List[str]]]:
        return parse_channel_command(content, prefixes=list(self.prefixes))

    def resolve(self, command: str) -> Optional[RegisteredChannelCommand]:
        marker = str(command or "").strip().lower()
        if not marker:
            return None
        canonical = self._alias_map.get(marker)
        if not canonical:
            return None
        return self._commands.get(canonical)

    def is_mutating(self, command: str, args: List[str]) -> bool:
        entry = self.resolve(command)
        if entry is None:
            return False
        rule = entry.mutating
        if callable(rule):
            try:
                return bool(rule(args))
            except Exception:
                return False
        return bool(rule)

    def execute(self, *, command: str, args: List[str], context: Any) -> Optional[str]:
        entry = self.resolve(command)
        if entry is None:
            return None
        return entry.handler(args, context)
