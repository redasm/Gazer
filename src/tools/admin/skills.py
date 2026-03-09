from __future__ import annotations

"""Skills management router — CRUD for built-in and extension skills."""

import os
from pathlib import Path
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException

from tools.admin.state import logger
from tools.admin.utils import _is_subpath
from .auth import verify_admin_token

router = APIRouter(tags=["skills"])

# Skill directory paths — relative to source tree
SKILLS_BUILTIN = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")
SKILLS_EXTENSION = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "skills")
SKILLS_BUILTIN_PATH = Path(SKILLS_BUILTIN)
SKILLS_EXTENSION_PATH = Path(SKILLS_EXTENSION)


def _scan_skill_dir(root: str, builtin: bool) -> list:
    """Scan a directory for skill folders containing SKILL.md."""
    results = []
    if not os.path.exists(root):
        return results
    for item in sorted(os.listdir(root)):
        item_path = os.path.join(root, item)
        skill_md = os.path.join(item_path, "SKILL.md")
        if not os.path.isdir(item_path) or not os.path.exists(skill_md):
            continue
        desc = "No description"
        with open(skill_md, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("description:"):
                    desc = line.replace("description:", "").strip()
                    break
        results.append({
            "name": item,
            "description": desc,
            "path": item_path,
            "builtin": builtin,
        })
    return results


def _resolve_skill_dir(name: str) -> str:
    """Resolve a skill name to its directory, checking both roots."""
    for root in (SKILLS_BUILTIN_PATH, SKILLS_EXTENSION_PATH):
        candidate = root / name
        if not _is_subpath(root, candidate):
            continue
        if candidate.is_dir() and (candidate / "SKILL.md").exists():
            return str(candidate)
    raise HTTPException(status_code=404, detail="Skill not found")


@router.get("/skills", dependencies=[Depends(verify_admin_token)])
async def list_skills():
    """List all skills (built-in + extension)."""
    skills = _scan_skill_dir(SKILLS_BUILTIN, builtin=True)
    seen = {s["name"] for s in skills}
    for s in _scan_skill_dir(SKILLS_EXTENSION, builtin=False):
        if s["name"] not in seen:
            skills.append(s)
            seen.add(s["name"])
    return skills


@router.post("/skills", dependencies=[Depends(verify_admin_token)])
async def create_skill(data: Dict[str, Any]):
    """Create a new extension skill folder with SKILL.md."""
    name = data.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Skill name required")

    name = "".join(c for c in name if c.isalnum() or c in ('_', '-'))
    skill_dir = SKILLS_EXTENSION_PATH / name

    if not _is_subpath(SKILLS_EXTENSION_PATH, skill_dir):
        raise HTTPException(status_code=403, detail="Path traversal detected")

    if skill_dir.exists():
        raise HTTPException(status_code=400, detail="Skill already exists")

    skill_dir.mkdir(parents=True, exist_ok=False)
    default_content = f"""---
name: {name}
description: New Skill
user-invocable: true
disable-model-invocation: false
---

# {name}

Describe your skill instructions here.
"""
    with open(skill_dir / "SKILL.md", "w", encoding="utf-8") as f:
        f.write(default_content)

    return {"status": "success", "name": name}


@router.get("/skills/{name}", dependencies=[Depends(verify_admin_token)])
async def get_skill_content(name: str, file: str = "SKILL.md"):
    skill_dir = Path(_resolve_skill_dir(name))
    target = (skill_dir / file).resolve(strict=False)
    if not _is_subpath(skill_dir, target):
        raise HTTPException(status_code=403, detail="Path traversal detected")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    with open(target, "r", encoding="utf-8") as f:
        content = f.read()
    return {"name": name, "file": file, "content": content}


@router.get("/skills/{name}/files", dependencies=[Depends(verify_admin_token)])
async def list_skill_files(name: str):
    skill_dir = _resolve_skill_dir(name)
    files = []
    for root, _dirs, filenames in os.walk(skill_dir):
        for fn in sorted(filenames):
            if fn.startswith(".") or fn.endswith(".pyc"):
                continue
            rel = os.path.relpath(os.path.join(root, fn), skill_dir).replace("\\", "/")
            files.append(rel)
    return {"name": name, "files": sorted(files)}


@router.put("/skills/{name}", dependencies=[Depends(verify_admin_token)])
async def update_skill_content(name: str, data: Dict[str, str]):
    skill_dir = Path(_resolve_skill_dir(name))
    file = data.get("file", "SKILL.md")
    target = (skill_dir / file).resolve(strict=False)
    if not _is_subpath(skill_dir, target):
        raise HTTPException(status_code=403, detail="Path traversal detected")
    content = data.get("content", "")
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(content)
    return {"status": "success"}
