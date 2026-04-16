"""Device registry initializer — local desktop and body hardware nodes."""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("GazerBrain")


def init_devices(
    config,
    device_registry,
    capture_manager,
    body,
    spatial,
    audio,
    ui_queue,
    rust_sidecar_client,
) -> Optional[Any]:
    """Populate *device_registry* with local desktop and body hardware nodes.

    Returns the (possibly newly-created) rust sidecar client so the caller
    can keep a reference for tool setup.
    """
    from runtime.rust_sidecar import build_rust_sidecar_client_from_config
    from devices.adapters.local_desktop import LocalDesktopNode
    from devices.adapters.body_hardware import BodyHardwareNode

    runtime_backend = str(config.get("runtime.backend", "python") or "python").strip().lower()
    if runtime_backend not in {"python", "rust"}:
        logger.warning("Unknown runtime.backend=%s, fallback to python.", runtime_backend)
        runtime_backend = "python"

    local_screen_requested = bool(config.get("perception.screen_enabled", True))
    local_backend = str(config.get("devices.local.backend", runtime_backend) or runtime_backend).strip().lower()
    if local_backend not in {"python", "rust"}:
        logger.warning("Unknown devices.local.backend=%s, fallback to python.", local_backend)
        local_backend = "python"

    # --- Local desktop node ---
    rust_client = None
    if local_backend == "rust":
        try:
            rust_client = rust_sidecar_client or build_rust_sidecar_client_from_config(config)
            rust_sidecar_client = rust_client
        except Exception as exc:
            logger.warning(
                "Failed to initialize rust sidecar client for local desktop node: %s. "
                "Fallback to python backend.", exc,
            )
            local_backend = "python"

    local_node_id = str(config.get("devices.local_node_id", "local-desktop")).strip() or "local-desktop"
    local_node_label = str(config.get("devices.local_node_label", "This Machine")).strip() or "This Machine"
    local_node = LocalDesktopNode(
        node_id=local_node_id,
        label=local_node_label,
        capture_manager=capture_manager if local_screen_requested else None,
        action_enabled=bool(config.get("perception.action_enabled", True)),
        backend=local_backend,
        rust_client=rust_client,
    )
    device_registry.register(local_node)

    # --- Body hardware node ---
    if bool(config.get("devices.body_node.enabled", True)):
        body_node_id = str(config.get("devices.body_node.node_id", "body-main")).strip() or "body-main"
        body_node_label = str(config.get("devices.body_node.label", "Physical Body")).strip() or "Physical Body"
        allow_connect_control = bool(config.get("devices.body_node.allow_connect_control", True))
        device_registry.register(
            BodyHardwareNode(
                body=body,
                node_id=body_node_id,
                label=body_node_label,
                allow_connect_control=allow_connect_control,
                spatial=spatial,
                audio=audio,
                ui_queue=ui_queue,
            )
        )

    return rust_sidecar_client
