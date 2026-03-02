"""YAML workflow parser and variable interpolation engine.

Parses ``.flow.yaml`` files into :class:`FlowDefinition` and resolves
``$args.*``, ``$steps.*``, ``$state.*``, ``$item`` references at runtime.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from flow.models import (
    FlowArg, FlowApproval, FlowConfig, FlowContext, FlowDefinition, FlowStep,
)

logger = logging.getLogger("FlowParser")

# Pattern for variable references: $args.foo, $steps.bar.output, $state.cursor, $item.field
_VAR_RE = re.compile(r"\$(?:args|steps|state|item)(?:\.[a-zA-Z0-9_\[\]\-]+)*")


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------

def parse_flow_file(path: Path) -> FlowDefinition:
    """Parse a ``.flow.yaml`` file into a :class:`FlowDefinition`."""
    with open(path, "r", encoding="utf-8") as fh:
        raw: Dict[str, Any] = yaml.safe_load(fh) or {}

    if "name" not in raw:
        raise ValueError(f"Workflow {path} missing required 'name' field")

    # Parse args
    args: Dict[str, FlowArg] = {}
    for k, v in raw.get("args", {}).items():
        if isinstance(v, dict):
            args[k] = FlowArg(name=k, type=v.get("type", "string"), default=v.get("default"))
        else:
            args[k] = FlowArg(name=k, default=v)

    # Parse steps
    steps: List[FlowStep] = []
    for s in raw.get("steps", []):
        approve = None
        if "approve" in s:
            a = s["approve"]
            approve = FlowApproval(prompt=a.get("prompt", ""), preview=a.get("preview"))
        depends_on = s.get("depends_on", []) or []
        if isinstance(depends_on, str):
            depends_on = [depends_on]
        elif not isinstance(depends_on, list):
            depends_on = []

        steps.append(FlowStep(
            id=s.get("id", f"step_{len(steps)}"),
            tool=s.get("tool"),
            args=s.get("args", {}),
            condition=s.get("condition"),
            approve=approve,
            depends_on=depends_on,
            retry_max=int(s.get("retry_max", 0) or 0),
            retry_backoff_ms=int(s.get("retry_backoff_ms", 0) or 0),
            timeout_ms=s.get("timeout_ms"),
            each=s.get("each"),
            on_complete=s.get("on_complete"),
        ))

    # Parse config
    cfg_raw = raw.get("config", {})
    flow_config = FlowConfig(
        timeout_ms=cfg_raw.get("timeout_ms", 60000),
        max_output_bytes=cfg_raw.get("max_output_bytes", 512000),
        retry_budget=int(cfg_raw.get("retry_budget", 8) or 8),
    )

    return FlowDefinition(
        name=raw["name"],
        description=raw.get("description", ""),
        args=args,
        state=raw.get("state", {}),
        steps=steps,
        config=flow_config,
        source_path=str(path),
    )


def discover_flows(search_dirs: List[Path]) -> Dict[str, FlowDefinition]:
    """Discover all ``.flow.yaml`` files in the given directories."""
    flows: Dict[str, FlowDefinition] = {}
    for d in search_dirs:
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.flow.yaml")):
            try:
                flow = parse_flow_file(path)
                if flow.name not in flows:
                    flows[flow.name] = flow
            except Exception as exc:
                logger.warning("Failed to parse flow %s: %s", path, exc)
    return flows


# ---------------------------------------------------------------------------
# Variable interpolation
# ---------------------------------------------------------------------------

def interpolate(value: Any, ctx: FlowContext) -> Any:
    """Resolve ``$var`` references in *value* using the flow context.

    - Strings containing ``$args.foo`` etc. are resolved.
    - If the entire string is a single ``$`` reference, the raw value is returned
      (preserving type, e.g. lists/dicts).
    - Dicts and lists are recursively interpolated.
    """
    if isinstance(value, str):
        return _interpolate_str(value, ctx)
    if isinstance(value, dict):
        return {k: interpolate(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v, ctx) for v in value]
    return value


def _interpolate_str(s: str, ctx: FlowContext) -> Any:
    """Resolve a single string value."""
    stripped = s.strip()

    # If the entire string is one variable reference, return raw value (preserve type)
    if _VAR_RE.fullmatch(stripped):
        return _resolve_ref(stripped, ctx)

    # Otherwise, do string substitution for each occurrence
    def _replace(m: re.Match) -> str:
        val = _resolve_ref(m.group(0), ctx)
        return str(val) if val is not None else ""

    return _VAR_RE.sub(_replace, s)


def _resolve_ref(ref: str, ctx: FlowContext) -> Any:
    """Resolve a single ``$`` reference like ``$steps.fetch.output``."""
    parts = ref.lstrip("$").split(".")

    # Root lookup
    root_name = parts[0]
    if root_name == "args":
        current: Any = ctx.args
    elif root_name == "steps":
        current = ctx.steps
    elif root_name == "state":
        current = ctx.state
    elif root_name == "item":
        current = ctx.item
        # Don't slice parts here — the shared loop below uses parts[1:]
        # which already skips the "item" root, just like args/steps/state.
    else:
        return ref  # Unknown root, return as-is

    # Drill into nested fields
    for part in parts[1:]:
        if current is None:
            return None
        # Handle array indexing like [0] or [-1]
        if part.startswith("[") and part.endswith("]"):
            try:
                idx = int(part[1:-1])
                current = current[idx]
            except (ValueError, IndexError, TypeError):
                return None
            continue
        # Dict / object access
        if isinstance(current, dict):
            current = current.get(part)
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return None

    return current
