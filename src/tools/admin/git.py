from __future__ import annotations

"""Git operations router — branch listing."""

import os
import subprocess as _subprocess
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException

from tools.admin.state import logger
from .auth import verify_admin_token

router = APIRouter(tags=["git"])


@router.get("/git/branches", dependencies=[Depends(verify_admin_token)])
async def get_git_branches():
    """List git branches with current branch indicator."""
    try:
        result = _subprocess.run(
            ["git", "branch", "-a", "--no-color"],
            capture_output=True, text=True, timeout=10,
            cwd=os.getcwd(),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="git is not installed")
    except _subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="git branch timed out")

    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stderr.strip())

    branches = []
    current = None
    for line in result.stdout.strip().splitlines():
        is_current = line.startswith("*")
        name = line.lstrip("* ").strip()
        if not name:
            continue
        if is_current:
            current = name
        branches.append({"name": name, "current": is_current})

    return {"current": current, "branches": branches}
