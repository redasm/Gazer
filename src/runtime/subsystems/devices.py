"""Device registry initializer — local desktop, satellite, body hardware nodes."""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("GazerBrain")


def init_devices(
    config,
    device_registry,
    capture_manager,
    satellite_session_manager,
    body,
    spatial,
    audio,
    ui_queue,
    rust_sidecar_client,
) -> Optional[Any]:
    """Populate *device_registry* with local / satellite / body nodes.

    Returns the (possibly newly-created) rust sidecar client so the caller
    can keep a reference for tool setup.
    """
    from runtime.rust_sidecar import build_rust_sidecar_client_from_config
    from devices.adapters.local_desktop import LocalDesktopNode
    from devices.adapters.remote_satellite import RemoteSatelliteNode
    from devices.adapters.body_hardware import BodyHardwareNode

    runtime_backend = str(config.get("runtime.backend", "python") or "python").strip().lower()
    if runtime_backend not in {"python", "rust"}:
        logger.warning("Unknown runtime.backend=%s, fallback to python.", runtime_backend)
        runtime_backend = "python"

    local_screen_requested = bool(config.get("perception.screen_enabled", True))
    satellite_ids = [
        str(sid).strip()
        for sid in config.get("perception.satellite_ids", [])
        if str(sid).strip()
    ]
    local_backend = str(config.get("devices.local.backend", runtime_backend) or runtime_backend).strip().lower()
    if local_backend not in {"python", "rust"}:
        logger.warning("Unknown devices.local.backend=%s, fallback to python.", local_backend)
        local_backend = "python"
    satellite_transport_backend = (
        str(config.get("satellite.transport_backend", runtime_backend) or runtime_backend).strip().lower()
    )
    if satellite_transport_backend not in {"python", "rust"}:
        logger.warning("Unknown satellite.transport_backend=%s, fallback to python.", satellite_transport_backend)
        satellite_transport_backend = "python"

    # --- Satellite nodes ---
    if satellite_ids:
        timeout_seconds = float(config.get("devices.satellite.invoke_timeout_seconds", 15) or 15)
        default_target = ""
        for idx, sid in enumerate(satellite_ids):
            node_cfg = config.get(f"devices.satellite.nodes.{sid}", {}) or {}
            allow_actions = node_cfg.get("allow_actions")
            if not isinstance(allow_actions, list) or not allow_actions:
                allow_actions = config.get("devices.satellite.default_allow_actions", [])
            label = str(node_cfg.get("label", sid)).strip() or sid
            node = RemoteSatelliteNode(
                node_id=sid,
                label=label,
                session_manager=satellite_session_manager,
                capture_manager=capture_manager,
                allow_actions=allow_actions,
                timeout_seconds=timeout_seconds,
            )
            device_registry.register(node)
            if idx == 0:
                default_target = sid
        if default_target:
            device_registry.default_target = default_target
        logger.info(
            "Device registry initialized in satellite mode with %d remote node(s), transport=%s.",
            len(satellite_ids), satellite_transport_backend,
        )

    # --- Local desktop node ---
    if not satellite_ids:
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
