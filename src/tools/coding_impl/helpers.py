"""Coding tools: helpers.

Extracted from coding.py.
"""

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional

from tools.base import ShellOperations, Tool, ToolSafetyTier

logger = logging.getLogger("CodingTools")

MAX_OUTPUT_CHARS = 100_000
MAX_READ_LINES = 10_000

# Image extensions recognized for auto-attach when ExecTool creates files
_IMAGE_SUFFIXES = frozenset(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'))

_NEXUM_MODULES_READY = False
_NEXUM_EDIT_DIFF_MODULE: Any = None
_NEXUM_EDIT_DIFF_LOADED = False


def _nexum_packages_root() -> Path:
    return Path(__file__).resolve().parents[3] / "external" / "Nexum" / "packages"


def _ensure_package_module(name: str) -> ModuleType:
    existing = sys.modules.get(name)
    if isinstance(existing, ModuleType):
        return existing

    module = ModuleType(name)
    module.__package__ = name
    module.__path__ = []  # type: ignore[attr-defined]
    sys.modules[name] = module
    parent_name, _, child = name.rpartition(".")
    if parent_name:
        parent = _ensure_package_module(parent_name)
        setattr(parent, child, module)
    return module


def _load_module_from_file(module_name: str, module_path: Path) -> Optional[Any]:
    if module_name in sys.modules:
        return sys.modules[module_name]
    if not module_path.is_file():
        return None

    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            logger.warning("Failed to build import spec: %s", module_name)
            return None
        module = importlib.util.module_from_spec(spec)
        parent_name, _, child = module_name.rpartition(".")
        if parent_name:
            parent = _ensure_package_module(parent_name)
            setattr(parent, child, module)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    except Exception:
        logger.warning("Failed to import module: %s", module_name, exc_info=True)
        sys.modules.pop(module_name, None)
        parent_name, _, child = module_name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None and hasattr(parent, child):
            delattr(parent, child)
        return None


def _ensure_nexum_modules() -> bool:
    global _NEXUM_MODULES_READY
    if _NEXUM_MODULES_READY:
        return True

    root = _nexum_packages_root()
    # New Nexum package structure: packages/<pkg>/src/nexum/<pkg>/
    core_types = root / "core" / "src" / "nexum" / "core" / "types.py"
    coding_path_utils = root / "coding" / "src" / "nexum" / "coding" / "path_utils.py"

    _ensure_package_module("nexum")
    _ensure_package_module("nexum.core")
    _ensure_package_module("nexum.coding")
    _ensure_package_module("nexum.coding.tools")

    if _load_module_from_file("nexum.core.types", core_types) is None:
        logger.warning("Nexum core types unavailable at %s", core_types)
        return False
    if _load_module_from_file("nexum.coding.path_utils", coding_path_utils) is None:
        logger.warning("Nexum path_utils unavailable at %s", coding_path_utils)
        return False

    _NEXUM_MODULES_READY = True
    return True


def _load_nexum_coding_tool_module(module_name: str) -> Optional[Any]:
    if not _ensure_nexum_modules():
        return None
    root = _nexum_packages_root()
    module_path = (
        root
        / "coding"
        / "src"
        / "nexum"
        / "coding"
        / "tools"
        / f"{module_name}.py"
    )
    full_name = f"nexum.coding.tools.{module_name}"
    module = _load_module_from_file(full_name, module_path)
    if module is None:
        logger.warning("Nexum tool module unavailable: %s", module_name)
    return module


def _load_nexum_edit_diff_module() -> Optional[Any]:
    global _NEXUM_EDIT_DIFF_MODULE
    global _NEXUM_EDIT_DIFF_LOADED

    if _NEXUM_EDIT_DIFF_LOADED:
        return _NEXUM_EDIT_DIFF_MODULE

    _NEXUM_EDIT_DIFF_LOADED = True
    _NEXUM_EDIT_DIFF_MODULE = _load_nexum_coding_tool_module("edit_diff")
    return _NEXUM_EDIT_DIFF_MODULE


def _create_nexum_tool(tool_name: str, cwd: str) -> Optional[Any]:
    module_name_map = {
        "bash": "bash",
        "read": "read",
        "write": "write",
        "edit": "edit",
        "ls": "ls",
        "find": "find",
        "grep": "grep",
    }
    factory_name_map = {
        "bash": "create_bash_tool",
        "read": "create_read_tool",
        "write": "create_write_tool",
        "edit": "create_edit_tool",
        "ls": "create_ls_tool",
        "find": "create_find_tool",
        "grep": "create_grep_tool",
    }
    module_name = module_name_map.get(tool_name)
    factory_name = factory_name_map.get(tool_name)
    if not module_name or not factory_name:
        return None
    module = _load_nexum_coding_tool_module(module_name)
    if module is None:
        return None
    factory = getattr(module, factory_name, None)
    if not callable(factory):
        logger.warning("Nexum factory missing: %s.%s", module_name, factory_name)
        return None
    return factory(cwd)


def _to_workspace_relative_path(workspace: Path, target: Path) -> str:
    try:
        rel = target.relative_to(workspace)
    except ValueError:
        return str(target)
    rel_str = str(rel).replace("\\", "/")
    return rel_str or "."


def _render_nexum_tool_result(result: Any) -> str:
    content = list(getattr(result, "content", []) or [])
    text_parts: List[str] = []
    for item in content:
        item_type = str(getattr(item, "type", "") or "")
        if item_type == "text":
            text_parts.append(str(getattr(item, "text", "") or ""))
        elif item_type == "image":
            mime = str(getattr(item, "mime_type", "image/unknown") or "image/unknown")
            text_parts.append(f"[image:{mime}]")

    details = getattr(result, "details", None)
    diff_text: Optional[str] = None
    if isinstance(details, dict):
        raw_diff = details.get("diff")
        if raw_diff:
            diff_text = str(raw_diff)
    elif hasattr(details, "diff"):
        raw_diff = getattr(details, "diff")
        if raw_diff:
            diff_text = str(raw_diff)

    rendered = "\n".join(part for part in text_parts if part).strip()
    if diff_text:
        rendered = (rendered + "\n" + diff_text).strip() if rendered else diff_text
    return rendered or "(no output)"


def _extract_structured_text(value: Any, depth: int = 0) -> Optional[str]:
    if depth > 6:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [_extract_structured_text(item, depth + 1) for item in value]
        merged = "".join(part for part in parts if isinstance(part, str))
        return merged or None
    if not isinstance(value, dict):
        return None

    if isinstance(value.get("text"), str):
        return str(value["text"])
    if isinstance(value.get("content"), str):
        return str(value["content"])
    if isinstance(value.get("content"), list):
        return _extract_structured_text(value.get("content"), depth + 1)
    if isinstance(value.get("parts"), list):
        return _extract_structured_text(value.get("parts"), depth + 1)
    raw_value = value.get("value")
    if isinstance(raw_value, str) and raw_value:
        raw_type = str(value.get("type", "") or "").lower()
        raw_kind = str(value.get("kind", "") or "").lower()
        if "text" in raw_type or raw_kind == "text":
            return raw_value
    return None


def _normalize_coding_params(params: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(params)

    def _is_blank(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == ""
        return False

    def _promote_alias(primary_key: str, alias_key: str) -> None:
        if alias_key not in normalized:
            return
        alias_value = normalized.pop(alias_key)
        if primary_key not in normalized or _is_blank(normalized.get(primary_key)):
            normalized[primary_key] = alias_value

    _promote_alias("path", "file_path")
    _promote_alias("old_text", "old_string")
    _promote_alias("new_text", "new_string")
    _promote_alias("old_text", "oldText")
    _promote_alias("new_text", "newText")

    for key in ("content", "old_text", "new_text"):
        extracted = _extract_structured_text(normalized.get(key))
        if isinstance(extracted, str):
            normalized[key] = extracted

    return normalized


async def _run_shell_command(
    *,
    shell_ops: Optional[ShellOperations],
    command: str,
    cwd: Path,
    timeout: int = 30,
) -> tuple[int, str, str]:
    if shell_ops is not None:
        return await shell_ops.exec(command, str(cwd), timeout=timeout)
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return (
        proc.returncode,
        stdout_bytes.decode(errors="replace"),
        stderr_bytes.decode(errors="replace"),
    )


class CodingToolBase(Tool):
    @property
    def provider(self) -> str:
        return "coding"

    @staticmethod
    def _error(code: str, message: str) -> str:
        return f"Error [{code}]: {message}"


