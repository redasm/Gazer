"""Skill loader -- discovers and manages Agent Skills (SKILL.md) from multiple directories."""

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional
from xml.sax.saxutils import escape

import yaml

from skills.base import SkillMetadata

logger = logging.getLogger("SkillLoader")


class SkillLoader:
    """
    Discovers Agent Skills from configured directories.

    Implements the Agent Skills integration pattern:
    1. Discovery -- scan directories for SKILL.md files
    2. Metadata -- parse frontmatter only (progressive disclosure)
    3. Activation -- load full instructions on demand
    4. Prompt injection -- format as XML for system prompt

    Directory priority (highest first):
      workspace/skills/ > ~/.gazer/skills/ > core/skills/
    """

    def __init__(self, dirs: List[Path]) -> None:
        self._dirs = dirs
        self._skills: Dict[str, SkillMetadata] = {}
        self._instructions_cache: Dict[str, str] = {}

    @property
    def skills(self) -> Dict[str, SkillMetadata]:
        return self._skills

    def discover(self) -> None:
        """Scan all configured directories for SKILL.md files.

        Later directories have lower priority -- if a skill name already
        exists from a higher-priority directory it is not overwritten.
        """
        for skill_dir in self._dirs:
            if not skill_dir.is_dir():
                continue
            for child in sorted(skill_dir.iterdir()):
                skill_md = child / "SKILL.md"
                if not child.is_dir() or not skill_md.is_file():
                    continue
                meta = self._parse_frontmatter(skill_md, child)
                if meta and meta.name not in self._skills:
                    self._skills[meta.name] = meta
                    logger.info("Discovered skill: %s (%s)", meta.name, child)

        logger.info("Total skills discovered: %s", len(self._skills))

    def get_instructions(self, name: str) -> str:
        """Load the full SKILL.md body for a skill (activation)."""
        if name in self._instructions_cache:
            return self._instructions_cache[name]

        meta = self._skills.get(name)
        if not meta:
            return ""

        skill_md = meta.path / "SKILL.md"
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError:
            return ""

        body = self._extract_body(content)
        self._instructions_cache[name] = body
        return body

    def format_for_prompt(self, preferred_order: Optional[List[str]] = None) -> str:
        """Generate <available_skills> XML for system prompt injection.

        Generates XML listing of discovered skills for LLM system prompt injection.
        """
        if not self._skills:
            return ""

        preferred = [str(item).strip() for item in (preferred_order or []) if str(item).strip()]
        preferred_index = {name: idx for idx, name in enumerate(preferred)}
        ordered_items = sorted(
            self._skills.values(),
            key=lambda meta: (
                preferred_index.get(meta.name, len(preferred_index) + 1000),
                meta.name.lower(),
            ),
        )

        lines = ["<available_skills>"]
        for meta in ordered_items:
            lines.append("  <skill>")
            lines.append(f"    <name>{escape(meta.name)}</name>")
            lines.append(f"    <description>{escape(meta.description)}</description>")
            lines.append(f"    <location>{escape(str(meta.path / 'SKILL.md'))}</location>")
            lines.append("  </skill>")
        lines.append("</available_skills>")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_frontmatter(skill_md: Path, skill_dir: Path) -> Optional[SkillMetadata]:
        """Parse YAML frontmatter from a SKILL.md file."""
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", skill_md, exc)
            return None

        if not content.startswith("---"):
            logger.warning("No frontmatter in %s", skill_md)
            return None

        end_idx = content.find("---", 3)
        if end_idx == -1:
            logger.warning("Malformed frontmatter in %s", skill_md)
            return None

        yaml_str = content[3:end_idx]
        try:
            data = yaml.safe_load(yaml_str) or {}
        except yaml.YAMLError as exc:
            logger.warning("YAML parse error in %s: %s", skill_md, exc)
            return None

        name = data.get("name")
        description = data.get("description", "")
        if not name:
            logger.warning("Missing 'name' in %s", skill_md)
            return None

        return SkillMetadata(
            name=name,
            description=description,
            path=skill_dir,
            allowed_tools=data.get("allowed-tools", ""),
            license=data.get("license", ""),
            compatibility=data.get("compatibility", ""),
            metadata=data.get("metadata", {}),
        )

    @staticmethod
    def _extract_body(content: str) -> str:
        """Extract the markdown body after YAML frontmatter."""
        if content.startswith("---"):
            end_idx = content.find("---", 3)
            if end_idx != -1:
                return content[end_idx + 3:].strip()
        return content.strip()
