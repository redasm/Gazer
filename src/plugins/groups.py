"""Tool group resolver for policy-based access control.

Resolves ``group:*`` references used in tool allow/deny lists and profiles.
Supports fnmatch-style wildcards within group definitions.
"""

import fnmatch
import logging
from typing import Any, Dict, List, Set

logger = logging.getLogger("ToolGroupResolver")

# Default group definitions (can be overridden via config)
DEFAULT_GROUPS: Dict[str, List[str]] = {
    "group:coding": [
        "exec", "read_file", "write_file", "edit_file",
        "list_dir", "find_files", "grep", "git_*",
    ],
    "group:web": ["web_search", "web_fetch"],
    "group:desktop": [
        "node_list", "node_describe", "node_invoke",
    ],
    "group:devices": ["node_list", "node_describe", "node_invoke"],
    "group:hardware": ["hardware_control", "vision_query"],
    "group:canvas": ["a2ui_apply", "canvas_snapshot", "canvas_reset"],
    "group:email": ["email_list", "email_read", "email_send", "email_search"],
    "group:automation": ["cron", "flow_run"],
}

# Predefined profiles (sets of groups/tools)
DEFAULT_PROFILES: Dict[str, List[str]] = {
    "coding": ["group:coding", "group:web", "browser"],
    "messaging": ["group:email", "read_skill"],
    "full": ["*"],
}


class ToolGroupResolver:
    """Resolve group references and profiles into concrete tool name sets.

    Usage::

        resolver = ToolGroupResolver(registered_tool_names, config_groups, config_profiles)
        allowed = resolver.resolve(["group:coding", "group:web", "browser"])
        # -> {"exec", "read_file", ..., "web_search", "web_fetch", "browser"}
    """

    def __init__(
        self,
        registered_tools: List[str],
        groups: Dict[str, List[str]] | None = None,
        profiles: Dict[str, List[str]] | None = None,
    ) -> None:
        self._registered = set(registered_tools)
        self._groups = {**DEFAULT_GROUPS, **(groups or {})}
        self._profiles = {**DEFAULT_PROFILES, **(profiles or {})}

    def update_registered(self, tool_names: List[str]) -> None:
        """Update the set of registered tool names (e.g. after plugins load)."""
        self._registered = set(tool_names)

    def resolve(self, specs: List[str]) -> Set[str]:
        """Resolve a list of specs into concrete tool names.

        A spec can be:
        - A literal tool name: ``"exec"``
        - A group reference: ``"group:coding"``
        - A wildcard: ``"*"`` (all registered tools)
        - A profile name used in context (resolved separately via ``resolve_profile``)
        """
        result: Set[str] = set()
        for spec in specs:
            if spec == "*":
                result |= self._registered
            elif spec.startswith("group:"):
                result |= self._expand_group(spec)
            else:
                # Literal tool name or fnmatch pattern
                matched = self._match_pattern(spec)
                result |= matched
        return result

    def resolve_profile(self, profile_name: str) -> Set[str]:
        """Resolve a profile name into concrete tool names."""
        specs = self._profiles.get(profile_name, [])
        return self.resolve(specs)

    def _expand_group(self, group_name: str) -> Set[str]:
        """Expand a group reference into tool names."""
        patterns = self._groups.get(group_name, [])
        result: Set[str] = set()
        for pattern in patterns:
            result |= self._match_pattern(pattern)
        return result

    def _match_pattern(self, pattern: str) -> Set[str]:
        """Match a pattern (possibly with wildcards) against registered tools."""
        if "*" in pattern or "?" in pattern or "[" in pattern:
            return {t for t in self._registered if fnmatch.fnmatch(t, pattern)}
        # Exact match
        if pattern in self._registered:
            return {pattern}
        return set()

    @property
    def group_names(self) -> List[str]:
        return list(self._groups.keys())

    @property
    def profile_names(self) -> List[str]:
        return list(self._profiles.keys())
