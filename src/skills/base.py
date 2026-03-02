"""Skill metadata -- pure data, no tool registration."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SkillMetadata:
    """Parsed SKILL.md frontmatter following the Agent Skills standard."""

    name: str
    description: str
    path: Path  # absolute path to the skill directory
    allowed_tools: str = ""  # space-delimited tool names (optional)
    license: str = ""
    compatibility: str = ""
    metadata: dict = field(default_factory=dict)
