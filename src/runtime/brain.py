"""Gazer Brain -- the central coordinator for perception, cognition and action."""

import logging
import asyncio
import json
from pathlib import Path
import os
from typing import Any, Dict, List, Optional

from runtime.config_manager import config
from perception.audio import get_audio
from hardware import create_body_driver, BodyDriver
from memory import MemoryManager
from memory.openviking_bootstrap import ensure_openviking_ready

from agent.adapter import GazerAgent
from channels.base import ChannelAdapter
from tools.coding import (
    ExecTool, ReadFileTool, WriteFileTool, EditFileTool,
    ListDirTool, FindFilesTool, GrepTool, ReadSkillTool,
)
from tools.admin.state import SATELLITE_SESSION_MANAGER, SATELLITE_SOURCES
from tools.system_tools import GetTimeTool, ImageAnalyzeTool
from plugins.hooks import HookRegistry
from plugins.loader import PluginLoader
from skills.loader import SkillLoader

# --- OpenClaw-inspired components ---
from scheduler.cron import CronScheduler
from scheduler.heartbeat import HeartbeatRunner
from llm.failover import FailoverProvider
from llm.litellm_provider import LiteLLMProvider
from tools.sandbox import get_sandbox_operations
from tools.remote_ops import get_ssh_operations
from runtime.rust_sidecar import (
    RustFileOperations,
    RustShellOperations,
    build_rust_sidecar_client_from_config,
)
from runtime.rust_gate import is_rust_gray_rollout_enabled
from tools.canvas import CanvasState
from gazer_email.client import EmailClient
from runtime.provider_registry import get_provider_registry
from devices.registry import DeviceRegistry
from devices.adapters.local_desktop import LocalDesktopNode
from devices.adapters.remote_satellite import RemoteSatelliteNode
from devices.adapters.body_hardware import BodyHardwareNode

from runtime.app_context import AppContext, set_app_context

from perception.capture import CaptureManager
from perception.sources.screen_local import LocalScreenSource
from perception.sources.screen_remote import RemoteScreenSource
from perception.sources.camera_local import LocalCameraSource

# Configure logging level from config, defaulting to INFO
_log_level_name = config.get("logging.level", "INFO").upper()
_log_level = getattr(logging, _log_level_name, logging.INFO)
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

# Install log sanitizer to prevent sensitive data leakage
from runtime.log_sanitizer import install_log_sanitizer
install_log_sanitizer(also_on_root=True)

logger = logging.getLogger("GazerBrain")


class GazerBrain:
    """Gazer Brain: coordinates perception, cognition, and action."""

    def __init__(self, ui_queue=None):
        ensure_openviking_ready(config)
        self.app_context = AppContext()
        set_app_context(self.app_context)
        
        # Initialize MemoryManager (OpenViking backend handles its own embedding configuration)
        self.memory_manager = MemoryManager()
        self.audio = get_audio()

        # --- Body driver (HAL) ---
        self.body: BodyDriver = create_body_driver(config)

        # --- Spatial perception (optional) ---
        self.spatial = None
        if config.get("perception.spatial_enabled", False):
            from perception.spatial import get_spatial
            self.spatial = get_spatial()

        self.ui_queue = ui_queue

        self.audio.set_queues(ui_queue)

        self.is_running = False
        self._rust_sidecar_client = None

        # Initialize Gazer Agent (provides MessageBus internally)
        workspace_path = Path(os.getcwd())
        self._sync_soul_single_source(workspace_path)
        self.agent = GazerAgent(workspace_path, self.memory_manager)
        
        self.app_context.llm_router = self.agent.router
        self.app_context.usage_tracker = self.agent.loop.usage
        self.app_context.prompt_cache_tracker = self.agent.loop.prompt_cache
        self.app_context.tool_batching_tracker = self.agent.loop.tool_batching_tracker
        self.app_context.trajectory_store = self.agent.loop.trajectory_store
        
        import tools.admin.state as _state
        _state.LLM_ROUTER = self.app_context.llm_router
        _state.USAGE_TRACKER = self.app_context.usage_tracker
        _state.PROMPT_CACHE_TRACKER = self.app_context.prompt_cache_tracker
        _state.TOOL_BATCHING_TRACKER = self.app_context.tool_batching_tracker
        _state.TRAJECTORY_STORE = self.app_context.trajectory_store

        # --- Cron Scheduler ---
        self.cron_scheduler: Optional[CronScheduler] = None
        self.heartbeat_runner: Optional[HeartbeatRunner] = None

        # --- Canvas / A2UI ---
        self.canvas_state: Optional[CanvasState] = None
        self._email_client: Optional[EmailClient] = None
        self._gmail_push_manager = None
        self._init_canvas()

        # --- Email client (optional, for email tools) ---
        self._init_email_client()

        # --- Webhook bus injection ---
        self._inject_webhook_globals()

        # --- Perception: CaptureManager + pluggable sources ---
        self.capture_manager: Optional[CaptureManager] = None
        self._init_capture()
        self.device_registry = DeviceRegistry(
            default_target=config.get("devices.default_target", "local-desktop"),
        )
        self._init_devices()

        # --- Channels (unified via ChannelAdapter) ---
        self.channels: List[ChannelAdapter] = []
        self._init_channels()

        self._agent_task = None
        self._cron_task = None
        self._heartbeat_task = None

    def _sync_soul_single_source(self, workspace_path: Path) -> None:
        """Use assets/SOUL.md as canonical soul source for runtime + prompt."""
        candidates = [
            workspace_path / "assets" / "SOUL.md",
            Path(__file__).resolve().parents[2] / "assets" / "SOUL.md",
        ]
        for path in candidates:
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if not content:
                continue
            current = str(config.get("personality.system_prompt", "") or "").strip()
            if current != content:
                config.set("personality.system_prompt", content)
                logger.info("SOUL single-source sync applied from %s", path)
            return

    def _init_capture(self) -> None:
        """Build CaptureManager and register context sources based on config."""
        interval = config.get("perception.capture_interval", 60)
        self.capture_manager = CaptureManager(self.memory_manager, capture_interval=interval)
        local_screen_requested = bool(config.get("perception.screen_enabled", True))
        satellite_ids: list = [
            str(source_id).strip()
            for source_id in config.get("perception.satellite_ids", [])
            if str(source_id).strip()
        ]
        local_screen_active = False

        if local_screen_requested and satellite_ids:
            logger.warning(
                "Both local and satellite screen perception are configured; "
                "exclusive mode enforces satellite-only. "
                "Set perception.screen_enabled=false to silence this warning."
            )

        # Enforce mutually exclusive mode:
        # satellite mode (if any satellite_ids) OR local mode (if enabled).
        if satellite_ids:
            for sid in satellite_ids:
                remote = RemoteScreenSource(source_id=sid)
                self.capture_manager.register_source(remote)
                # Expose to Admin API so the WebSocket endpoint can push frames
                SATELLITE_SOURCES[sid] = remote
            screen_mode = "satellite"
        elif local_screen_requested:
            if LocalScreenSource.is_available():
                self.capture_manager.register_source(LocalScreenSource())
                local_screen_active = True
                screen_mode = "local"
            else:
                logger.warning(
                    "perception.screen_enabled=true but local screen capture dependency is "
                    "missing; install with: pip install mss pillow"
                )
                screen_mode = "disabled"
        else:
            screen_mode = "disabled"
        logger.info(
            "Perception screen mode: %s (local_requested=%s, local_active=%s, satellite_ids=%s)",
            screen_mode,
            local_screen_requested,
            local_screen_active,
            satellite_ids,
        )

        # Local camera
        if config.get("perception.camera_enabled", False):
            self.capture_manager.register_source(LocalCameraSource())

    def _init_devices(self) -> None:
        runtime_backend = str(config.get("runtime.backend", "python") or "python").strip().lower()
        if runtime_backend not in {"python", "rust"}:
            logger.warning("Unknown runtime.backend=%s, fallback to python.", runtime_backend)
            runtime_backend = "python"

        local_screen_requested = bool(config.get("perception.screen_enabled", True))
        satellite_ids = [
            str(source_id).strip()
            for source_id in config.get("perception.satellite_ids", [])
            if str(source_id).strip()
        ]
        local_backend = str(config.get("devices.local.backend", runtime_backend) or runtime_backend).strip().lower()
        if local_backend not in {"python", "rust"}:
            logger.warning("Unknown devices.local.backend=%s, fallback to python.", local_backend)
            local_backend = "python"
        satellite_transport_backend = (
            str(config.get("satellite.transport_backend", runtime_backend) or runtime_backend).strip().lower()
        )
        if satellite_transport_backend not in {"python", "rust"}:
            logger.warning(
                "Unknown satellite.transport_backend=%s, fallback to python.",
                satellite_transport_backend,
            )
            satellite_transport_backend = "python"

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
                    session_manager=SATELLITE_SESSION_MANAGER,
                    capture_manager=self.capture_manager,
                    allow_actions=allow_actions,
                    timeout_seconds=timeout_seconds,
                )
                self.device_registry.register(node)
                if idx == 0:
                    default_target = sid
            if default_target:
                self.device_registry.default_target = default_target
            logger.info(
                "Device registry initialized in satellite mode with %d remote node(s), transport=%s.",
                len(satellite_ids),
                satellite_transport_backend,
            )

        if not satellite_ids:
            rust_client = None
            if local_backend == "rust":
                try:
                    rust_client = self._rust_sidecar_client or build_rust_sidecar_client_from_config(config)
                    self._rust_sidecar_client = rust_client
                except Exception as exc:
                    logger.warning(
                        "Failed to initialize rust sidecar client for local desktop node: %s. "
                        "Fallback to python backend.",
                        exc,
                    )
                    local_backend = "python"

            local_node_id = (
                str(config.get("devices.local_node_id", "local-desktop")).strip()
                or "local-desktop"
            )
            local_node_label = (
                str(config.get("devices.local_node_label", "This Machine")).strip()
                or "This Machine"
            )
            local_node = LocalDesktopNode(
                node_id=local_node_id,
                label=local_node_label,
                capture_manager=self.capture_manager if local_screen_requested else None,
                action_enabled=bool(config.get("perception.action_enabled", True)),
                backend=local_backend,
                rust_client=rust_client,
            )
            self.device_registry.register(local_node)

        if bool(config.get("devices.body_node.enabled", True)):
            body_node_id = str(config.get("devices.body_node.node_id", "body-main")).strip() or "body-main"
            body_node_label = (
                str(config.get("devices.body_node.label", "Physical Body")).strip()
                or "Physical Body"
            )
            allow_connect_control = bool(
                config.get("devices.body_node.allow_connect_control", True)
            )
            self.device_registry.register(
                BodyHardwareNode(
                    body=self.body,
                    node_id=body_node_id,
                    label=body_node_label,
                    allow_connect_control=allow_connect_control,
                    spatial=self.spatial,
                    audio=self.audio,
                    ui_queue=self.ui_queue,
                )
            )

    def _init_email_client(self) -> None:
        """Initialize optional email client for email tools."""
        if config.get("email.enabled", False):
            self._email_client = EmailClient(
                imap_host=config.get("email.imap_host", "imap.gmail.com"),
                imap_port=config.get("email.imap_port", 993),
                smtp_host=config.get("email.smtp_host", "smtp.gmail.com"),
                smtp_port=config.get("email.smtp_port", 587),
                username=config.get("email.username", ""),
                password=config.get("email.password", "") or os.getenv("GAZER_EMAIL_PASSWORD", ""),
                max_body_length=config.get("email.max_body_length", 8000),
            )
            logger.info("Email client initialized.")

    def _init_canvas(self) -> None:
        """Initialize Canvas/A2UI state and inject into admin API."""
        if not config.get("canvas.enabled", True):
            return
        from tools.admin_api import _canvas_on_change
        self.canvas_state = CanvasState(
            max_panels=config.get("canvas.max_panels", 20),
            max_content_size=config.get("canvas.max_content_size", 65536),
            on_change=_canvas_on_change,
        )
        self.app_context.canvas_state = self.canvas_state
        import tools.admin.state as _state_canvas
        _state_canvas.CANVAS_STATE = self.app_context.canvas_state
        logger.info("Canvas/A2UI initialized.")

    def _inject_webhook_globals(self) -> None:
        """Inject MessageBus and hook token into admin_api webhook endpoints."""
        self.app_context.hook_bus = self.agent.bus
        self.app_context.hook_token = config.get("hooks.token", "") or None
        import tools.admin.state as _state_hook
        _state_hook.HOOK_BUS = self.app_context.hook_bus
        _state_hook.HOOK_TOKEN = self.app_context.hook_token

    def _init_channels(self) -> None:
        """Create and bind all configured channels."""
        bus = self.agent.bus

        from channels.base import ChannelRegistry
        import importlib

        # Force import of all channels so they register themselves
        # Non-installed optional channels will be skipped gracefully
        for mod_name in [
            "channels.discord",
            "channels.feishu",
            "channels.google_chat",
            "channels.signal_channel",
            "channels.slack",
            "channels.teams",
            "channels.telegram",
            "channels.web",
            "channels.whatsapp",
        ]:
            try:
                importlib.import_module(mod_name)
            except ImportError as e:
                logger.info("Skipping channel module %s (missing dependencies: %s)", mod_name, e)

        for name, channel_cls in ChannelRegistry.get_all().items():
            try:
                channel = channel_cls.from_config(
                    config,
                    ui_queue=self.ui_queue,
                )
                if channel:
                    channel.bind(bus)
                    self.channels.append(channel)

                    # Inject into admin API state for webhook routing
                    if name == "whatsapp":
                        import tools.admin.state as _state_wa
                        _state_wa.WHATSAPP_CHANNEL = channel
                    elif name == "teams":
                        import tools.admin.state as _state_teams
                        _state_teams.TEAMS_CHANNEL = channel
                    elif name == "google_chat":
                        import tools.admin.state as _state_gchat
                        _state_gchat.GOOGLE_CHAT_CHANNEL = channel
            except Exception as e:
                logger.error("Failed to load channel %s: %s", name, e, exc_info=True)

        # -- Channel init summary --
        activated_names = [ch.channel_name for ch in self.channels]
        all_registered = list(ChannelRegistry.get_all().keys())
        skipped_names = [n for n in all_registered if n not in activated_names]
        if activated_names:
            logger.info("Channels activated: %s", ", ".join(activated_names))
        if skipped_names:
            logger.warning("Channels registered but not activated (disabled or missing credentials): %s", ", ".join(skipped_names))

        # Gmail Pub/Sub push manager (event-driven automation, no email chat channel)
        if config.get("gmail_push.enabled", False):
            self._init_gmail_push()

    def _init_gmail_push(self) -> None:
        """Set up Gmail Pub/Sub push manager and inject into admin API."""
        from gazer_email.gmail_push import GmailPushManager
        from bus.events import InboundMessage

        async def _on_gmail_messages(messages: List[dict]):
            """Called when Gmail push detects new messages.

            OpenClaw-style path: route as an automation event through MessageBus
            instead of using an email chat channel.
            """
            if not messages:
                return
            message_ids = [str(item.get("gmail_id", "")) for item in messages if item.get("gmail_id")]
            compact_messages: List[dict] = []
            lines = []
            for idx, item in enumerate(messages[:5], start=1):
                from_address = str(item.get("from_address", "")).strip()
                from_raw = str(item.get("from", "")).strip()
                subject = str(item.get("subject", "")).strip()
                body_text = str(item.get("body_text", "")).strip()
                snippet = str(item.get("snippet", "")).strip()
                message_id = str(item.get("message_id", "")).strip()

                body_preview = (body_text or snippet).replace("\n", " ").strip()
                if len(body_preview) > 320:
                    body_preview = body_preview[:320] + "..."

                compact_messages.append(
                    {
                        "gmail_id": str(item.get("gmail_id", "")),
                        "from": from_raw,
                        "from_address": from_address,
                        "subject": subject,
                        "message_id": message_id,
                        "body_preview": body_preview,
                    }
                )
                lines.append(
                    f"{idx}. from={from_address or from_raw} | subject={subject or '(no subject)'}"
                )
                if body_preview:
                    lines.append(f"   preview={body_preview}")

            payload = {
                "source": "gmail",
                "event_type": "new_messages",
                "message_ids": message_ids,
                "count": len(messages),
                "messages": compact_messages,
            }
            summary = (
                f"[External Event: gmail/new_messages]\n"
                f"Detected {len(messages)} new Gmail message(s).\n"
                f"{chr(10).join(lines)}\n"
                "You can auto-reply with email_send using:\n"
                "- to = from_address\n"
                "- subject = 'Re: ' + original subject\n"
                "- reply_to = message_id\n"
                "Only reply when a response is actually needed."
            )
            msg = InboundMessage(
                channel="webhook",
                chat_id="event:gmail:main",
                sender_id="hook:gmail",
                content=summary,
                metadata=payload,
            )
            try:
                await self.agent.bus.publish_inbound(msg)
            except ValueError as exc:
                logger.warning("Gmail push event dropped by bus policy: %s", exc)
            except Exception:
                logger.exception("Failed to publish Gmail push event to bus")

        self._gmail_push_manager = GmailPushManager(
            credentials_file=config.get("gmail_push.credentials_file", "config/gmail_credentials.json"),
            token_file=config.get("gmail_push.token_file", "config/gmail_token.json"),
            topic=config.get("gmail_push.topic", ""),
            history_store=config.get("gmail_push.history_store", "data/gmail_history.json"),
            on_new_messages=_on_gmail_messages,
        )
        import tools.admin.state as _state_gmail
        _state_gmail.GMAIL_PUSH_MANAGER = self._gmail_push_manager
        logger.info("Gmail Pub/Sub push manager configured.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _update_ui_status(self, text: str):
        if self.ui_queue:
            self.ui_queue.put({"type": "status", "data": text})

    def _init_hardware(self):
        self.body.connect()
        if self.spatial:
            self.spatial.start()

    async def _setup_tools(self):
        workspace = Path(os.getcwd())
        coding_workspace = workspace

        # =============================================================
        # Hook Registry — lifecycle hooks for tool calls
        # =============================================================
        self.hook_registry = HookRegistry()
        self.agent.loop.tools.set_hook_registry(self.hook_registry)

        # =============================================================
        # Layer 1: Hardcoded core tools (~10, always available)
        # =============================================================
        sandbox_ops = get_sandbox_operations(config, sidecar_client=self._rust_sidecar_client)
        shell_ops = None
        file_ops = None
        runtime_backend = str(config.get("runtime.backend", "python") or "python").strip().lower()
        exec_backend = str(config.get("coding.exec_backend", "local") or "local").strip().lower()
        if exec_backend == "local" and runtime_backend == "rust":
            exec_backend = "rust"
        allow_local_fallback = bool(config.get("coding.allow_local_fallback", False))
        if exec_backend == "sandbox":
            if sandbox_ops:
                shell_ops = sandbox_ops[0]
                file_ops = sandbox_ops[1]
                logger.info("ExecTool backend set to sandbox (Docker).")
            else:
                msg = "coding.exec_backend=sandbox but sandbox is unavailable."
                if allow_local_fallback:
                    logger.warning("%s Falling back to local.", msg)
                else:
                    raise RuntimeError(f"{msg} Set coding.allow_local_fallback=true to allow fallback.")
        elif exec_backend == "ssh":
            ssh_enabled = bool(config.get("coding.ssh.enabled", False))
            ssh_host = str(config.get("coding.ssh.host", "") or "").strip()
            if ssh_enabled and ssh_host:
                try:
                    shell_ops, file_ops = get_ssh_operations(
                        config,
                        sidecar_client=self._rust_sidecar_client,
                    )
                    remote_workspace = str(config.get("coding.ssh.remote_workspace", ".") or ".").strip()
                    coding_workspace = Path(remote_workspace)
                    backend = str(config.get("runtime.backend", "python") or "python").strip().lower()
                    logger.info("ExecTool backend set to SSH (%s) via %s.", ssh_host, backend)
                except Exception as exc:
                    if allow_local_fallback:
                        logger.warning("Failed to initialize SSH exec backend: %s. Falling back to local.", exc)
                    else:
                        raise RuntimeError(
                            "Failed to initialize SSH exec backend and fallback is disabled."
                        ) from exc
            else:
                msg = "coding.exec_backend=ssh but coding.ssh.enabled/host is not configured."
                if allow_local_fallback:
                    logger.warning("%s Falling back to local.", msg)
                else:
                    raise RuntimeError(f"{msg} Set coding.allow_local_fallback=true to allow fallback.")
        elif exec_backend == "rust":
            try:
                sidecar_client = self._rust_sidecar_client or build_rust_sidecar_client_from_config(config)
                probe = await sidecar_client.probe_minimal()
                self._rust_sidecar_client = sidecar_client
                fallback_shell_ops = None
                fallback_file_ops = None
                if allow_local_fallback or is_rust_gray_rollout_enabled(config):
                    from tools.base import ShellOperations as _LocalShellOperations, FileOperations as _LocalFileOperations

                    fallback_shell_ops = _LocalShellOperations()
                    fallback_file_ops = _LocalFileOperations()
                shell_ops = RustShellOperations(
                    sidecar_client,
                    fallback_shell_ops=fallback_shell_ops,
                )
                file_ops = RustFileOperations(
                    sidecar_client,
                    fallback_file_ops=fallback_file_ops,
                )
                logger.info(
                    "ExecTool backend set to rust sidecar at %s.",
                    probe.get("endpoint", sidecar_client.endpoint),
                )
                logger.info(
                    "Rust sidecar probe ok: health=%s, version=%s",
                    probe.get("health", {}),
                    probe.get("version", {}),
                )
            except Exception as exc:
                msg = f"coding.exec_backend=rust but rust sidecar is unavailable: {exc}"
                if allow_local_fallback:
                    logger.warning("%s Falling back to local.", msg)
                else:
                    raise RuntimeError(f"{msg} Set coding.allow_local_fallback=true to allow fallback.")
        elif exec_backend != "local":
            msg = f"Unknown coding.exec_backend={exec_backend}."
            if allow_local_fallback:
                logger.warning("%s Falling back to local.", msg)
            else:
                raise RuntimeError(f"{msg} Set coding.allow_local_fallback=true to allow fallback.")

        exec_tool = ExecTool(
            coding_workspace,
            shell_ops=shell_ops,
        )

        read_skill_tool = ReadSkillTool()
        for tool in [
            exec_tool,
            ReadFileTool(coding_workspace, file_ops=file_ops, shell_ops=shell_ops),
            WriteFileTool(coding_workspace, file_ops=file_ops),
            EditFileTool(coding_workspace, file_ops=file_ops),
            ListDirTool(coding_workspace, shell_ops=shell_ops),
            FindFilesTool(coding_workspace, shell_ops=shell_ops),
            GrepTool(coding_workspace, shell_ops=shell_ops),
            GetTimeTool(),
            ImageAnalyzeTool(),
            read_skill_tool,
        ]:
            self.agent.register_tool(tool)

        # =============================================================
        # Prepare runtime services for plugins
        # =============================================================
        cron_enabled = bool(config.get("scheduler.cron_enabled", True))
        if cron_enabled:
            self.cron_scheduler = CronScheduler(
                run_callback=self._run_cron_job,
            )
            self.cron_scheduler.load()
            self.app_context.cron_scheduler = self.cron_scheduler
            import tools.admin.state as _state_cron
            _state_cron.CRON_SCHEDULER = self.app_context.cron_scheduler

        services = {
            "capture_manager": self.capture_manager,
            "device_registry": self.device_registry,
            "body": self.body,
            "spatial": self.spatial,
            "canvas_state": self.canvas_state,
            "email_client": self._email_client,
            "cron_scheduler": self.cron_scheduler,
            "coding_shell_ops": shell_ops,
            "coding_file_ops": file_ops,
            "coding_workspace": coding_workspace,
        }

        # =============================================================
        # Layer 2 + 3: All non-core tools via PluginLoader
        # =============================================================
        self.plugin_loader = PluginLoader(workspace=workspace)
        self.plugin_loader.discover()

        # Skill loader must be ready before plugins (they may register skill dirs)
        skills_dirs = [
            workspace / "skills",
            Path.home() / ".gazer" / "skills",
            Path(__file__).resolve().parent / "skills",
        ]
        skill_loader = SkillLoader(skills_dirs)
        skill_loader.discover()

        loaded_ids = self.plugin_loader.load_all(
            tool_registry=self.agent.loop.tools,
            hook_registry=self.hook_registry,
            workspace=workspace,
            bus=self.agent.bus,
            memory=self.memory_manager,
            skill_loader=skill_loader,
            services=services,
        )
        if loaded_ids:
            logger.info(f"Loaded plugins: {loaded_ids}")
        if self.plugin_loader.failed_ids:
            logger.warning(f"Failed plugins: {self.plugin_loader.failed_ids}")

        # =============================================================
        # GazerFlow — deterministic workflow engine
        # =============================================================
        from flow.engine import FlowEngine
        from flow.tool import FlowRunTool

        flow_dirs = [
            workspace / "workflows",
            Path.home() / ".gazer" / "workflows",
        ]
        self.flow_engine = FlowEngine(
            tool_registry=self.agent.loop.tools,
            llm_provider=self.agent.provider,
            flow_dirs=flow_dirs,
        )
        self.agent.register_tool(FlowRunTool(self.flow_engine))
        logger.info(f"GazerFlow loaded {len(self.flow_engine.list_flows())} workflow(s).")

        # =============================================================
        # Tool security policy
        # =============================================================
        denylist = config.get("security.tool_denylist", [])
        allowlist = config.get("security.tool_allowlist", [])
        if denylist:
            self.agent.loop.tools.set_denylist(denylist)
        if allowlist:
            self.agent.loop.tools.set_allowlist(allowlist)

        # Inject registry into Admin API for policy explain/simulate endpoints
        self.app_context.tool_registry = self.agent.loop.tools
        import tools.admin.state as _state_policy
        _state_policy.TOOL_REGISTRY = self.app_context.tool_registry

        logger.info(f"Registered {len(self.agent.loop.tools)} tools.")

        # =============================================================
        # Skill loader finalization
        # =============================================================
        self.agent.set_skill_loader(skill_loader)
        read_skill_tool.set_skill_loader(skill_loader)
        logger.info(f"Loaded {len(skill_loader.skills)} skills into context.")

    async def _start_admin_api(self) -> None:
        """Start the Admin API (uvicorn) as an asyncio task in the same event loop."""
        import uvicorn
        from tools.admin_api import app, init_admin_api

        api_port = int(os.environ.get("ADMIN_API_PORT", config.get("web.port", 8080)))
        host = os.environ.get("ADMIN_API_HOST", "127.0.0.1")

        init_admin_api(self.app_context)

        uvi_config = uvicorn.Config(app, host=host, port=api_port, log_level="info")
        server = uvicorn.Server(uvi_config)
        await server.serve()

    async def _run_cron_job(self, job) -> Optional[str]:
        """Callback for the cron scheduler — runs a job as an agent turn."""
        try:
            result = await self.agent.process_auto(
                content=job.message, sender="cron",
            )
            # Optionally deliver to a channel
            if job.delivery_channel and job.delivery_chat_id:
                from bus.events import OutboundMessage
                await self.agent.bus.publish_outbound(OutboundMessage(
                    channel=job.delivery_channel,
                    chat_id=job.delivery_chat_id,
                    content=f"[Cron: {job.name}]\n{result}",
                ))
            return result
        except Exception as exc:
            logger.error(f"Cron job {job.id} execution error: {exc}")
            return f"Error: {exc}"

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    async def start(self):
        logger.info("Gazer Brain starting...")
        self.is_running = True
        self._update_ui_status("Initializing...")

        await self._setup_tools()

        # Start Admin API server as asyncio task (same event loop)
        self._api_server_task = asyncio.create_task(self._start_admin_api())

        self._agent_task = asyncio.create_task(self.agent.start())

        # Start Cron Scheduler
        if self.cron_scheduler:
            self._cron_task = asyncio.create_task(self.cron_scheduler.start())
            logger.info("CronScheduler started.")

        # Start Heartbeat Runner
        if config.get("scheduler.heartbeat_enabled", True):
            workspace_path = Path(os.getcwd())
            self.heartbeat_runner = HeartbeatRunner(
                workspace=workspace_path,
                run_callback=self._run_heartbeat,
                interval_seconds=config.get("scheduler.heartbeat_interval", 300),
            )
            self._heartbeat_task = asyncio.create_task(self.heartbeat_runner.start())
            logger.info("HeartbeatRunner started.")

        # Start all channels
        channel_tasks = []
        for ch in self.channels:
            task = asyncio.create_task(ch.start())
            channel_tasks.append(task)
            logger.info(f"Channel '{ch.channel_name}' activated.")

        # Start Gmail Pub/Sub watch (if configured)
        if self._gmail_push_manager:
            gmail_ok = await self._gmail_push_manager.setup()
            if gmail_ok:
                logger.info("Gmail Pub/Sub watch registered.")
            else:
                logger.warning("Gmail Pub/Sub setup failed (missing deps or credentials).")

        # Start CaptureManager (passive perception loop)
        if self.capture_manager:
            await self.capture_manager.start()

        self._init_hardware()

        # --- Voice wake word integration ---
        if config.get("wake_word.enabled", False):
            self._setup_wake_word()

        self._update_ui_status("Vision Active")

        while self.is_running:
            try:
                config.check_reload()
                self._inject_webhook_globals()

                if self.spatial:
                    attention = self.spatial.get_attention_level()
                    is_present = attention > 0
                else:
                    is_present = False

                if not is_present:
                    self.body.gesture("breathe")

                await asyncio.sleep(0.1)

            except Exception as e:
                logger.error(f"Brain loop error: {e}", exc_info=True)
                await asyncio.sleep(1)

        for task in channel_tasks:
            task.cancel()

    def _setup_wake_word(self) -> None:
        """Wire GazerAudio wake word detection to publish InboundMessages."""
        keyword = config.get("wake_word.keyword", "gazer")
        logger.info(f"Wake word enabled: '{keyword}'")

        # Monkey-patch a wake callback into GazerAudio if it has ASR
        if self.audio and self.audio.asr_model:
            original_transcribe = self.audio.record_and_transcribe

            def _transcribe_with_wake(*args, **kwargs):
                text = original_transcribe(*args, **kwargs)
                asr_meta = {}
                get_meta = getattr(self.audio, "get_last_asr_meta", None)
                if callable(get_meta):
                    try:
                        payload = get_meta()
                        if isinstance(payload, dict):
                            asr_meta = payload
                    except Exception:
                        asr_meta = {}
                if text and keyword.lower() in text.lower():
                    # Publish wake event to the message bus
                    import asyncio
                    from bus.events import InboundMessage
                    metadata = {"source": "wake_word"}
                    if asr_meta:
                        metadata["asr"] = asr_meta
                    msg = InboundMessage(
                        channel="voice",
                        chat_id="wake",
                        sender_id="owner",
                        content=text,
                        metadata=metadata,
                    )
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self.agent.bus.publish_inbound(msg))
                    except RuntimeError:
                        pass
                    logger.info(
                        "Wake word detected: %s | asr=%s",
                        text,
                        json.dumps(asr_meta, ensure_ascii=False)[:240] if asr_meta else "{}",
                    )
                return text

            self.audio.record_and_transcribe = _transcribe_with_wake

    async def _run_heartbeat(self, prompt: str) -> Optional[str]:
        """Callback for the heartbeat runner."""
        try:
            return await self.agent.process_auto(content=prompt, sender="heartbeat")
        except Exception as exc:
            logger.error(f"Heartbeat execution error: {exc}")
            return f"Error: {exc}"

    def stop(self):
        self.is_running = False
        if self.spatial:
            self.spatial.stop()
        self.body.disconnect()
        if self.capture_manager:
            try:
                asyncio.ensure_future(self.capture_manager.stop())
            except RuntimeError:
                pass  # No running event loop
        # Stop new components
        if self.cron_scheduler:
            self.cron_scheduler.stop()
        if self.heartbeat_runner:
            self.heartbeat_runner.stop()
        if self._gmail_push_manager:
            self._gmail_push_manager.stop()
        if hasattr(self, "agent"):
            self.agent.stop()
        if self._agent_task:
            self._agent_task.cancel()
        for task in (self._cron_task, self._heartbeat_task):
            if task:
                task.cancel()
        logger.info("Gazer Brain stopped.")
