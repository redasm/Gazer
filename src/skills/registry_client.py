"""Skill registry client -- search and install skills from a remote index.

The default index is a simple JSON manifest hosted on GitHub (or any URL).
Structure of the index file::

    [
      {"name": "github", "description": "GitHub workflow skill", "url": "https://..."},
      ...
    ]
"""

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SkillRegistry")


@dataclass
class SkillInfo:
    """Metadata for a skill in the remote registry."""

    name: str
    description: str
    url: str  # URL to SKILL.md or a zip archive
    version: str = ""
    author: str = ""


class SkillRegistryClient:
    """Minimal client for a remote skill index.

    The index is a JSON array of ``SkillInfo``-like objects served at
    ``registry_url``.
    """

    def __init__(self, registry_url: str = "") -> None:
        self._url = registry_url or "https://raw.githubusercontent.com/gazer-ai/skill-index/main/index.json"
        self._cache: Optional[List[SkillInfo]] = None

    async def _fetch_index(self) -> List[SkillInfo]:
        if self._cache is not None:
            return self._cache
        try:
            import urllib.request
            req = urllib.request.Request(self._url, headers={"User-Agent": "Gazer/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self._cache = [
                SkillInfo(
                    name=s.get("name", ""),
                    description=s.get("description", ""),
                    url=s.get("url", ""),
                    version=s.get("version", ""),
                    author=s.get("author", ""),
                )
                for s in data
                if s.get("name")
            ]
            logger.info(f"Fetched {len(self._cache)} skills from registry")
        except Exception as exc:
            logger.warning(f"Failed to fetch skill index: {exc}")
            self._cache = []
        return self._cache

    async def search(self, query: str) -> List[SkillInfo]:
        """Search for skills matching *query* (case-insensitive substring)."""
        index = await self._fetch_index()
        q = query.lower()
        return [
            s for s in index
            if q in s.name.lower() or q in s.description.lower()
        ]

    async def install(self, name: str, target_dir: Path) -> str:
        """Download and install a skill into *target_dir*.

        Returns a status message.
        """
        index = await self._fetch_index()
        match = next((s for s in index if s.name == name), None)
        if not match:
            return f"Skill '{name}' not found in registry."

        skill_dir = target_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / "SKILL.md"

        try:
            import urllib.request
            req = urllib.request.Request(match.url, headers={"User-Agent": "Gazer/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read().decode("utf-8")
            skill_md.write_text(content, encoding="utf-8")
            return f"Installed skill '{name}' to {skill_dir}"
        except Exception as exc:
            return f"Failed to install '{name}': {exc}"

    def invalidate_cache(self) -> None:
        self._cache = None


# ---------------------------------------------------------------------------
# Agent-facing tools
# ---------------------------------------------------------------------------

from tools.base import Tool, ToolSafetyTier


class SkillSearchTool(Tool):
    """Search the remote skill registry."""

    def __init__(self, client: SkillRegistryClient) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "skill_search"

    @property
    def description(self) -> str:
        return "Search the remote skill registry for skills by keyword."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword."},
            },
            "required": ["query"],
        }

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.SAFE

    async def execute(self, query: str = "", **kwargs: Any) -> str:
        results = await self._client.search(query)
        if not results:
            return f"No skills found for '{query}'."
        lines = [f"- {s.name}: {s.description}" for s in results[:20]]
        return "\n".join(lines)


class SkillInstallTool(Tool):
    """Install a skill from the remote registry."""

    def __init__(self, client: SkillRegistryClient, target_dir: Path) -> None:
        self._client = client
        self._target = target_dir

    @property
    def name(self) -> str:
        return "skill_install"

    @property
    def description(self) -> str:
        return "Install a skill from the remote registry by name."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name to install."},
            },
            "required": ["name"],
        }

    @property
    def safety_tier(self) -> ToolSafetyTier:
        return ToolSafetyTier.STANDARD

    async def execute(self, name: str = "", **kwargs: Any) -> str:
        if not name:
            return "Error: skill name is required."
        return await self._client.install(name, self._target)
