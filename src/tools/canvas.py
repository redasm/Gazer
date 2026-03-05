"""Canvas / A2UI surface -- agent-driven structured visual workspace.

Provides a ``CanvasState`` manager that holds named panels of content
(markdown, tables, charts, forms, JSON, text, A2UI surfaces) and
broadcasts updates to connected WebSocket clients via a callback.

Default tools exposed to the agent:

* **a2ui_apply**      -- apply Google A2UI protocol messages
* **canvas_snapshot** -- read the current canvas state
* **canvas_reset**    -- clear panels
"""

import copy
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from tools.base import Tool

logger = logging.getLogger("Canvas")

# ---------------------------------------------------------------------------
# Allowed panel content types
# ---------------------------------------------------------------------------
ALLOWED_CONTENT_TYPES = {"markdown", "table", "chart", "form", "json", "text", "a2ui"}

A2UI_OPERATION_KEYS = {
    "surfaceUpdate",
    "dataModelUpdate",
    "beginRendering",
    "deleteSurface",
}

OnChangeCallback = Callable[["CanvasState", Optional[Dict[str, Any]]], Awaitable[None]]


class CanvasToolBase(Tool):
    @property
    def provider(self) -> str:
        return "canvas"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class CanvasPanel:
    """A single panel on the canvas."""

    id: str
    content_type: str  # one of ALLOWED_CONTENT_TYPES
    content: str  # the actual payload
    title: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class A2UISurface:
    """In-memory A2UI surface snapshot."""

    id: str
    catalog_id: str = "standard"
    root: str = ""
    components: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    data_model: Any = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# CanvasState
# ---------------------------------------------------------------------------

class CanvasState:
    """In-memory canvas state.

    Holds an ordered list of panels.  Every mutation increments a version
    counter and invokes ``on_change`` so that connected WebSocket clients
    are notified.
    """

    def __init__(
        self,
        max_panels: int = 20,
        max_content_size: int = 65536,
        on_change: Optional[OnChangeCallback] = None,
    ) -> None:
        self.max_panels = max_panels
        self.max_content_size = max_content_size
        self.on_change = on_change

        self._panels: Dict[str, CanvasPanel] = {}
        self._order: List[str] = []  # insertion order
        self._surfaces: Dict[str, A2UISurface] = {}
        self.version: int = 0

    # -- read --

    @property
    def panels(self) -> List[CanvasPanel]:
        return [self._panels[pid] for pid in self._order if pid in self._panels]

    def get_panel(self, panel_id: str) -> Optional[CanvasPanel]:
        return self._panels.get(panel_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "panels": [asdict(p) for p in self.panels],
            "surfaces": self.surface_snapshots(),
        }

    def get_surface(self, surface_id: str) -> Optional[A2UISurface]:
        return self._surfaces.get(surface_id)

    def surface_snapshots(self) -> List[Dict[str, Any]]:
        return [self._surface_snapshot(s) for s in self._surfaces.values()]

    # -- write --

    async def push(
        self,
        panel_id: str,
        content_type: str,
        content: str,
        title: str = "",
        meta: Optional[Dict[str, Any]] = None,
    ) -> CanvasPanel:
        """Add or replace a panel."""
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ValueError(
                f"Invalid content_type '{content_type}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
            )
        if len(content) > self.max_content_size:
            content = content[: self.max_content_size]
            logger.warning(f"Panel {panel_id} content truncated to {self.max_content_size} bytes")

        now = time.time()
        if panel_id in self._panels:
            panel = self._panels[panel_id]
            panel.content_type = content_type
            panel.content = content
            panel.title = title or panel.title
            panel.updated_at = now
            if meta:
                panel.meta.update(meta)
        else:
            # Enforce max panels
            if len(self._panels) >= self.max_panels:
                # Evict oldest
                oldest = self._order.pop(0)
                del self._panels[oldest]
                logger.info(f"Evicted oldest panel '{oldest}' (max={self.max_panels})")

            panel = CanvasPanel(
                id=panel_id,
                content_type=content_type,
                content=content,
                title=title,
                created_at=now,
                updated_at=now,
                meta=meta or {},
            )
            self._panels[panel_id] = panel
            self._order.append(panel_id)

        self.version += 1
        await self._notify()
        return panel

    async def reset(self, panel_id: Optional[str] = None) -> int:
        """Clear all panels or a specific one.  Returns removed count."""
        removed = 0
        if panel_id:
            if panel_id in self._panels:
                del self._panels[panel_id]
                self._order.remove(panel_id)
                removed = 1
        else:
            removed = len(self._panels)
            self._panels.clear()
            self._order.clear()
            self._surfaces.clear()

        self.version += 1
        await self._notify()
        return removed

    async def apply_a2ui_messages(
        self,
        messages: Sequence[Dict[str, Any]],
        default_surface_id: str = "main",
    ) -> Dict[str, Any]:
        """Apply one or more Google A2UI protocol messages.
        """
        if not messages:
            raise ValueError("At least one A2UI message is required.")

        resolved_default_surface = self._normalize_surface_id(default_surface_id or "main")
        touched_surfaces: set[str] = set()
        deleted_surfaces: set[str] = set()
        operations: List[str] = []
        changed = False

        for raw_message in messages:
            operation, payload = self._extract_a2ui_operation(raw_message)
            surface_id = self._normalize_surface_id(
                payload.get("surfaceId")
                or resolved_default_surface
            )

            if operation == "beginRendering":
                surface = self._ensure_surface(surface_id)
                catalog_id = str(
                    payload.get("catalog")
                    or surface.catalog_id
                    or "standard"
                ).strip()
                if catalog_id:
                    surface.catalog_id = catalog_id
                surface.updated_at = time.time()
                touched_surfaces.add(surface_id)
                changed = True

            elif operation == "surfaceUpdate":
                surface = self._ensure_surface(surface_id)
                components = self._normalize_components(payload.get("components"))
                if not components:
                    raise ValueError(f"A2UI {operation} requires non-empty 'components'.")
                surface.components.update(components)
                root = str(payload.get("root") or "").strip()
                if root:
                    surface.root = root
                surface.updated_at = time.time()
                touched_surfaces.add(surface_id)
                changed = True

            elif operation == "dataModelUpdate":
                surface = self._ensure_surface(surface_id)
                path = str(payload.get("path", "")).strip()
                contents = payload.get("contents")
                if not isinstance(contents, list):
                    raise ValueError("A2UI dataModelUpdate requires 'contents' array.")
                contents_map = self._decode_data_entries(contents)
                self._set_data_model_value(surface, path, contents_map, mode="merge")
                surface.updated_at = time.time()
                touched_surfaces.add(surface_id)
                changed = True

            elif operation == "deleteSurface":
                self._surfaces.pop(surface_id, None)
                panel_id = self._surface_panel_id(surface_id)
                if panel_id in self._panels:
                    del self._panels[panel_id]
                    try:
                        self._order.remove(panel_id)
                    except ValueError:
                        pass
                deleted_surfaces.add(surface_id)
                changed = True

            operations.append(operation)

        pushed_any_surface = False
        for surface_id in sorted(touched_surfaces):
            if surface_id in self._surfaces:
                await self._sync_surface_panel(surface_id)
                pushed_any_surface = True

        if changed and not pushed_any_surface:
            self.version += 1
            await self._notify()

        return {
            "message_count": len(messages),
            "operations": operations,
            "surface_ids": sorted(touched_surfaces),
            "deleted_surface_ids": sorted(deleted_surfaces),
        }

    # -- internal --

    async def _notify(self, extra: Optional[Dict[str, Any]] = None) -> None:
        if self.on_change:
            try:
                await self.on_change(self, extra)
            except Exception as exc:
                logger.debug(f"Canvas on_change callback error: {exc}")

    def _ensure_surface(self, surface_id: str) -> A2UISurface:
        existing = self._surfaces.get(surface_id)
        if existing is not None:
            return existing
        surface = A2UISurface(id=surface_id)
        self._surfaces[surface_id] = surface
        return surface

    @staticmethod
    def _normalize_surface_id(raw_surface_id: Any) -> str:
        surface_id = str(raw_surface_id or "main").strip()
        return surface_id or "main"

    @staticmethod
    def _surface_panel_id(surface_id: str) -> str:
        return f"a2ui:{surface_id}"

    @classmethod
    def _extract_a2ui_operation(cls, message: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        if not isinstance(message, dict):
            raise ValueError("A2UI message must be an object.")

        for key in A2UI_OPERATION_KEYS:
            payload = message.get(key)
            if isinstance(payload, dict):
                return key, payload

        raise ValueError(
            "Unsupported A2UI message. Expected one of: "
            + ", ".join(sorted(A2UI_OPERATION_KEYS))
        )

    @staticmethod
    def _normalize_components(raw: Any) -> Dict[str, Dict[str, Any]]:
        normalized: Dict[str, Dict[str, Any]] = {}
        if isinstance(raw, dict):
            for component_id, definition in raw.items():
                safe_component_id = str(component_id).strip()
                if not safe_component_id or not isinstance(definition, dict):
                    continue
                normalized[safe_component_id] = definition
            return normalized

        return normalized

    @staticmethod
    def _deep_merge(base: Dict[str, Any], incoming: Dict[str, Any]) -> None:
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                CanvasState._deep_merge(base[key], value)
            else:
                base[key] = value

    @staticmethod
    def _normalize_data_path(path: str) -> List[str]:
        cleaned = str(path or "").strip()
        if not cleaned or cleaned == "$":
            return []
        cleaned = cleaned.replace("/", ".")
        if cleaned.startswith("$."):
            cleaned = cleaned[2:]
        cleaned = cleaned.lstrip(".")
        return [part for part in cleaned.split(".") if part]

    @classmethod
    def _set_data_model_value(
        cls,
        surface: A2UISurface,
        path: str,
        contents: Any,
        *,
        mode: str = "replace",
    ) -> None:
        path_parts = cls._normalize_data_path(path)
        merge_mode = mode == "merge"

        if not path_parts:
            if merge_mode and isinstance(surface.data_model, dict) and isinstance(contents, dict):
                cls._deep_merge(surface.data_model, contents)
            else:
                surface.data_model = contents
            return

        if not isinstance(surface.data_model, dict):
            surface.data_model = {}
        cursor: Dict[str, Any] = surface.data_model
        for part in path_parts[:-1]:
            next_value = cursor.get(part)
            if not isinstance(next_value, dict):
                next_value = {}
                cursor[part] = next_value
            cursor = next_value

        leaf = path_parts[-1]
        existing = cursor.get(leaf)
        if merge_mode and isinstance(existing, dict) and isinstance(contents, dict):
            cls._deep_merge(existing, contents)
        else:
            cursor[leaf] = contents

    @classmethod
    def _decode_data_entries(cls, entries: Sequence[Any]) -> Dict[str, Any]:
        decoded: Dict[str, Any] = {}
        for raw_entry in entries:
            if not isinstance(raw_entry, dict):
                continue
            key = str(raw_entry.get("key") or "").strip()
            if not key:
                continue
            decoded[key] = cls._decode_data_value(raw_entry)
        return decoded

    @classmethod
    def _decode_data_value(cls, raw: Dict[str, Any]) -> Any:
        if "valueString" in raw:
            return str(raw.get("valueString", ""))
        if "valueNumber" in raw:
            return raw.get("valueNumber")
        if "valueBoolean" in raw:
            return bool(raw.get("valueBoolean"))
        if "valueMap" in raw and isinstance(raw.get("valueMap"), list):
            return cls._decode_data_entries(raw.get("valueMap", []))
        if "valueList" in raw and isinstance(raw.get("valueList"), list):
            out: List[Any] = []
            for item in raw.get("valueList", []):
                if isinstance(item, dict):
                    out.append(cls._decode_data_value(item))
                else:
                    out.append(item)
            return out
        if "valueNull" in raw:
            return None
        return None

    def _surface_snapshot(self, surface: A2UISurface) -> Dict[str, Any]:
        return {
            "surfaceId": surface.id,
            "catalogId": surface.catalog_id,
            "root": surface.root,
            "components": copy.deepcopy(surface.components),
            "dataModel": copy.deepcopy(surface.data_model),
            "createdAt": surface.created_at,
            "updatedAt": surface.updated_at,
        }

    async def _sync_surface_panel(self, surface_id: str) -> None:
        surface = self._surfaces.get(surface_id)
        if surface is None:
            return

        snapshot = self._surface_snapshot(surface)
        content = json.dumps(snapshot, ensure_ascii=False)
        if len(content) > self.max_content_size:
            compact = {
                "surfaceId": surface.id,
                "catalogId": surface.catalog_id,
                "root": surface.root,
                "error": "A2UI surface payload exceeds canvas.max_content_size",
                "componentCount": len(surface.components),
            }
            content = json.dumps(compact, ensure_ascii=False)

        await self.push(
            panel_id=self._surface_panel_id(surface.id),
            content_type="a2ui",
            content=content,
            title=f"A2UI / {surface.id}",
            meta={"source": "a2ui", "surfaceId": surface.id, "catalogId": surface.catalog_id},
        )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class A2UIApplyTool(CanvasToolBase):
    """Apply Google A2UI protocol messages to canvas surfaces."""

    def __init__(self, canvas: CanvasState) -> None:
        self._canvas = canvas

    @property
    def name(self) -> str:
        return "a2ui_apply"


    @property
    def description(self) -> str:
        return (
            "Apply one or more Google A2UI protocol messages to structured UI surfaces. "
            "Supports v0.8 operations: beginRendering, surfaceUpdate, dataModelUpdate, deleteSurface."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "object",
                    "description": "Single A2UI protocol message.",
                },
                "messages": {
                    "type": "array",
                    "description": "Batch of A2UI protocol messages.",
                    "items": {"type": "object"},
                },
                "surfaceId": {
                    "type": "string",
                    "description": "Default surface ID when a message omits surfaceId.",
                },
            },
            "required": [],
        }

    async def execute(
        self,
        message: Optional[Dict[str, Any]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        surfaceId: str = "main",
        **_: Any,
    ) -> str:
        batch: List[Dict[str, Any]] = []
        if isinstance(message, dict):
            batch.append(message)
        if isinstance(messages, list):
            batch.extend(item for item in messages if isinstance(item, dict))

        if not batch:
            return "Error: provide 'message' or 'messages' with A2UI protocol payloads."

        try:
            summary = await self._canvas.apply_a2ui_messages(
                batch,
                default_surface_id=surfaceId or "main",
            )
            return (
                f"A2UI applied ({summary['message_count']} message(s), "
                f"surfaces={summary['surface_ids']}, deleted={summary['deleted_surface_ids']})."
            )
        except ValueError as exc:
            return f"Error: {exc}"


class CanvasSnapshotTool(CanvasToolBase):
    """Return the current canvas state as JSON."""

    def __init__(self, canvas: CanvasState) -> None:
        self._canvas = canvas

    @property
    def name(self) -> str:
        return "canvas_snapshot"


    @property
    def description(self) -> str:
        return "Get the current canvas state: list of all panels with their content."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **_: Any) -> str:
        state = self._canvas.to_dict()
        if not state["panels"]:
            return "Canvas is empty (no panels)."
        return json.dumps(state, ensure_ascii=False, indent=2)


class CanvasResetTool(CanvasToolBase):
    """Clear the canvas (all panels or a specific one)."""

    def __init__(self, canvas: CanvasState) -> None:
        self._canvas = canvas

    @property
    def name(self) -> str:
        return "canvas_reset"


    @property
    def description(self) -> str:
        return "Clear the visual canvas. Optionally specify panel_id to remove a single panel."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "panel_id": {
                    "type": "string",
                    "description": "Panel to remove (omit to clear all).",
                },
            },
            "required": [],
        }

    async def execute(self, panel_id: str = "", **_: Any) -> str:
        removed = await self._canvas.reset(panel_id or None)
        if panel_id:
            return f"Panel '{panel_id}' removed." if removed else f"Panel '{panel_id}' not found."
        return f"Canvas cleared ({removed} panel(s) removed)."
