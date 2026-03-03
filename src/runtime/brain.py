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
from tools.system_tools import GetTimeTool, ImageAnalyzeTool
from plugins.hooks import HookRegistry
from plugins.loader import PluginLoader
from skills.loader import SkillLoader

# --- OpenClaw-inspired components ---
from bus.command_queue import CommandQueue, CommandLane
from agent.orchestrator import AgentOrchestrator, AgentConfig, AgentBinding
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
import tools.admin.state as _admin_state

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


class _IpcLogHandler(logging.Handler):
    """Forward log records from the Brain process to the Admin API process via IPC queue.

    Captures the same structured metadata (request_id, model, tokens) as the
    Admin API's GazerLogHandler so that LLM call info shows up in the web log viewer.
    """
    _META_KEYS = ("request_id", "model", "tokens")

    def __init__(self, queue) -> None:
        super().__init__()
        self._queue = queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from datetime import datetime
            entry = {
                "timestamp": datetime.fromtimestamp(record.created).isoformat(),
                "level": record.levelname,
                "source": record.name,
                "message": record.getMessage(),
            }
            meta = {}
            for key in self._META_KEYS:
                val = getattr(record, key, None)
                if val is not None:
                    meta[key] = val
            if meta:
                entry["meta"] = meta
            self._queue.put({"type": "log_entry", "entry": entry})
        except Exception:
            pass


class GazerBrain:
    """Gazer Brain: coordinates perception, cognition, and action."""

    def __init__(self, ui_queue=None, ipc_input=None, ipc_output=None):
        ensure_openviking_ready(config)
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
        self._ipc_input = ipc_input
        self._ipc_output = ipc_output

        self.audio.set_queues(ui_queue)

        self.is_running = False
        self._rust_sidecar_client = None

        # Initialize Gazer Agent (provides MessageBus internally)
        workspace_path = Path(os.getcwd())
        self._sync_soul_single_source(workspace_path)
        self.agent = GazerAgent(workspace_path, self.memory_manager)
        import tools.admin.state as _state
        _state.LLM_ROUTER = self.agent.router
        _state.USAGE_TRACKER = self.agent.loop.usage
        _state.PROMPT_CACHE_TRACKER = self.agent.loop.prompt_cache
        _state.TOOL_BATCHING_TRACKER = self.agent.loop.tool_batching_tracker
        _state.TRAJECTORY_STORE = self.agent.loop.trajectory_store

        # --- Lane-based Command Queue ---
        self.command_queue = CommandQueue()

        # --- Multi-Agent Orchestrator ---
        self.orchestrator = AgentOrchestrator(
            command_queue=self.command_queue,
            provider=self.agent.provider,
            bus=self.agent.bus,
            max_parallel_tasks=int(config.get("agents.orchestrator.max_parallel_tasks", 3) or 3),
            max_parallel_per_agent=int(config.get("agents.orchestrator.max_parallel_per_agent", 2) or 2),
            max_pending_tasks=int(config.get("agents.orchestrator.max_pending_tasks", 64) or 64),
            default_timeout_seconds=float(
                config.get("agents.orchestrator.sla.timeout_seconds", 120.0) or 120.0
            ),
            default_max_retries=int(config.get("agents.orchestrator.sla.max_retries", 0) or 0),
            default_retry_backoff_seconds=float(
                config.get("agents.orchestrator.sla.retry_backoff_seconds", 0.0) or 0.0
            ),
            default_priority=str(
                config.get("agents.orchestrator.sla.priority", "normal") or "normal"
            ).strip().lower(),
            default_resource_lock_timeout_seconds=float(
                config.get("agents.orchestrator.resource_lock_timeout_seconds", 30.0) or 30.0
            ),
            sleep_poll_interval_seconds=float(
                config.get("agents.orchestrator.sleep_wake.poll_interval_seconds", 1.0) or 1.0
            ),
            max_sleep_seconds=float(
                config.get("agents.orchestrator.sleep_wake.max_sleep_seconds", 3600.0) or 3600.0
            ),
        )
        self._init_orchestrator()
        _admin_state.ORCHESTRATOR = self.orchestrator

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
        self._init_channels(ipc_input, ipc_output)

        self._agent_task = None
        self._cq_task = None
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
        from tools.admin._shared import SATELLITE_SOURCES
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
            from tools.admin._shared import SATELLITE_SESSION_MANAGER
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

    def _init_orchestrator(self) -> None:
        """Register sub-agents and bindings from config."""
        workspace_path = Path(os.getcwd())
        # Always register the default/main agent
        self.orchestrator.register_agent(AgentConfig(
            id="main", name="Gazer", workspace=workspace_path, is_default=True,
        ))
        # Additional agents from config
        for agent_def in config.get("agents.list", []):
            self.orchestrator.register_agent(AgentConfig(
                id=agent_def.get("id", ""),
                name=agent_def.get("name", ""),
                workspace=Path(agent_def.get("workspace", str(workspace_path))),
                model=agent_def.get("model"),
                tool_policy=agent_def.get("tool_policy"),
                is_default=False,
            ))
        for bind_def in config.get("agents.bindings", []):
            self.orchestrator.add_binding(AgentBinding(
                agent_id=bind_def.get("agent_id", "main"),
                channel=bind_def.get("channel"),
                chat_id=bind_def.get("chat_id"),
                sender_id=bind_def.get("sender_id"),
            ))


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
        import tools.admin.state as _state_canvas
        _state_canvas.CANVAS_STATE = self.canvas_state
        logger.info("Canvas/A2UI initialized.")

    def _inject_webhook_globals(self) -> None:
        """Inject MessageBus and hook token into admin_api webhook endpoints."""
        import tools.admin.state as _state_hook
        _state_hook.HOOK_BUS = self.agent.bus
        _state_hook.HOOK_TOKEN = config.get("hooks.token", "") or None

    def _init_channels(self, ipc_input, ipc_output) -> None:
        """Create and bind all configured channels."""
        bus = self.agent.bus

        # Telegram
        tg_token = config.get("telegram.token")
        allowed_ids = config.get("telegram.allowed_ids", [])
        if config.get("telegram.enabled") and tg_token:
            from channels.telegram import TelegramChannel
            tg = TelegramChannel(tg_token, allowed_ids)
            tg.bind(bus)
            self.channels.append(tg)

        # Gmail Pub/Sub push manager (event-driven automation, no email chat channel)
        if config.get("gmail_push.enabled", False):
            self._init_gmail_push()

        # Feishu / Lark
        feishu_app_id = str(
            config.get("feishu.app_id", "") or os.getenv("FEISHU_APP_ID", "")
        ).strip()
        feishu_app_secret = str(
            config.get("feishu.app_secret", "") or os.getenv("FEISHU_APP_SECRET", "")
        ).strip()
        if config.get("feishu.enabled") and feishu_app_id and feishu_app_secret:
            from channels.feishu import FeishuChannel
            feishu_allowed = config.get("feishu.allowed_ids", [])
            feishu = FeishuChannel(feishu_app_id, feishu_app_secret, feishu_allowed)
            feishu.bind(bus)
            self.channels.append(feishu)
        elif config.get("feishu.enabled"):
            logger.warning(
                "Feishu channel enabled but credentials are missing. "
                "Set feishu.app_id/feishu.app_secret in settings.yaml "
                "or FEISHU_APP_ID/FEISHU_APP_SECRET in environment."
            )

        # Discord
        discord_token = config.get("discord.token", "")
        if config.get("discord.enabled") and discord_token:
            from channels.discord import DiscordChannel

            discord_allowed_guild_ids = config.get("discord.allowed_guild_ids", [])
            discord = DiscordChannel(discord_token, discord_allowed_guild_ids)
            discord.bind(bus)
            self.channels.append(discord)

        # WhatsApp (Cloud API)
        wa_phone_id = str(
            config.get("whatsapp.phone_number_id", "") or os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
        ).strip()
        wa_token = str(
            config.get("whatsapp.access_token", "") or os.getenv("WHATSAPP_ACCESS_TOKEN", "")
        ).strip()
        if config.get("whatsapp.enabled") and wa_phone_id and wa_token:
            from channels.whatsapp import WhatsAppChannel
            wa = WhatsAppChannel(
                phone_number_id=wa_phone_id,
                access_token=wa_token,
                verify_token=str(
                    config.get("whatsapp.verify_token", "") or os.getenv("WHATSAPP_VERIFY_TOKEN", "")
                ).strip(),
                webhook_secret=str(
                    config.get("whatsapp.webhook_secret", "") or os.getenv("WHATSAPP_WEBHOOK_SECRET", "")
                ).strip(),
                api_version=config.get("whatsapp.api_version", "v21.0"),
            )
            wa.bind(bus)
            self.channels.append(wa)
            # Inject into admin API for webhook routing
            import tools.admin.state as _state_wa
            _state_wa.WHATSAPP_CHANNEL = wa
        elif config.get("whatsapp.enabled"):
            logger.warning(
                "WhatsApp channel enabled but credentials are missing. "
                "Set whatsapp.phone_number_id/whatsapp.access_token in settings.yaml "
                "or WHATSAPP_PHONE_NUMBER_ID/WHATSAPP_ACCESS_TOKEN in environment."
            )

        # Signal (via signal-cli REST API)
        signal_api = str(
            config.get("signal.api_url", "") or os.getenv("SIGNAL_API_URL", "")
        ).strip()
        signal_phone = str(
            config.get("signal.phone_number", "") or os.getenv("SIGNAL_PHONE_NUMBER", "")
        ).strip()
        if config.get("signal.enabled") and signal_api and signal_phone:
            from channels.signal_channel import SignalChannel
            sig = SignalChannel(api_url=signal_api, phone_number=signal_phone)
            sig.bind(bus)
            self.channels.append(sig)
        elif config.get("signal.enabled"):
            logger.warning(
                "Signal channel enabled but api_url/phone_number missing."
            )

        # Microsoft Teams (Bot Framework)
        teams_app_id = str(
            config.get("teams.app_id", "") or os.getenv("TEAMS_APP_ID", "")
        ).strip()
        teams_app_secret = str(
            config.get("teams.app_secret", "") or os.getenv("TEAMS_APP_SECRET", "")
        ).strip()
        if config.get("teams.enabled") and teams_app_id and teams_app_secret:
            from channels.teams import TeamsChannel
            teams = TeamsChannel(app_id=teams_app_id, app_secret=teams_app_secret)
            teams.bind(bus)
            self.channels.append(teams)
            import tools.admin.state as _state_teams
            _state_teams.TEAMS_CHANNEL = teams
        elif config.get("teams.enabled"):
            logger.warning(
                "Teams channel enabled but app_id/app_secret missing."
            )

        # Google Chat
        gchat_sa = str(
            config.get("google_chat.service_account_file", "") or os.getenv("GOOGLE_CHAT_SA_FILE", "")
        ).strip()
        gchat_project = str(
            config.get("google_chat.project_id", "") or os.getenv("GOOGLE_CHAT_PROJECT_ID", "")
        ).strip()
        if config.get("google_chat.enabled"):
            from channels.google_chat import GoogleChatChannel
            gchat = GoogleChatChannel(
                service_account_file=gchat_sa,
                project_id=gchat_project,
            )
            gchat.bind(bus)
            self.channels.append(gchat)
            import tools.admin.state as _state_gchat
            _state_gchat.GOOGLE_CHAT_CHANNEL = gchat

        # Web Chat (IPC queues from the desktop UI)
        if ipc_input:
            from channels.web import WebChannel
            web = WebChannel(ipc_input, ipc_output, ui_queue=self.ui_queue)
            web.bind(bus)
            self.channels.append(web)

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
        if cron_enabled and self._ipc_input is None:
            self.cron_scheduler = CronScheduler(
                run_callback=self._run_cron_job,
            )
            self.cron_scheduler.load()
            import tools.admin.state as _state_cron
            _state_cron.CRON_SCHEDULER = self.cron_scheduler
        elif cron_enabled:
            logger.info("Cron scheduler delegated to Admin API process (IPC mode).")

        services = {
            "capture_manager": self.capture_manager,
            "device_registry": self.device_registry,
            "body": self.body,
            "spatial": self.spatial,
            "canvas_state": self.canvas_state,
            "email_client": self._email_client,
            "cron_scheduler": self.cron_scheduler,
            "orchestrator": self.orchestrator,
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
        import tools.admin.state as _state_policy
        _state_policy.TOOL_REGISTRY = self.agent.loop.tools

        logger.info(f"Registered {len(self.agent.loop.tools)} tools.")

        # =============================================================
        # Skill loader finalization
        # =============================================================
        self.agent.set_skill_loader(skill_loader)
        read_skill_tool.set_skill_loader(skill_loader)
        logger.info(f"Loaded {len(skill_loader.skills)} skills into context.")

    def _register_ipc_usage_hook(self) -> None:
        """Register an after-turn hook that pushes usage & router snapshots to the Admin API process."""
        ipc_out = self._ipc_output
        usage_tracker = self.agent.loop.usage
        router = self.agent.router

        async def _push_usage(payload: Dict[str, Any]) -> None:
            try:
                msg: Dict[str, Any] = {
                    "type": "usage_update",
                    "usage": usage_tracker.summary(),
                }
                if router is not None and hasattr(router, "get_status"):
                    msg["router_status"] = router.get_status()
                ipc_out.put(msg)
            except Exception:
                pass

        self.agent.turn_hooks.on_after_turn(_push_usage)

    async def _run_cron_job(self, job) -> Optional[str]:
        """Callback for the cron scheduler — runs a job as an agent turn."""
        try:
            result = await self.agent.process_message(
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
        # Install IPC log handler to forward Brain-process logs to Admin API
        if hasattr(self, '_ipc_output') and self._ipc_output:
            ipc_handler = _IpcLogHandler(self._ipc_output)
            ipc_handler.setLevel(logging.INFO)
            logging.getLogger().addHandler(ipc_handler)

        logger.info("Gazer Brain starting...")
        self.is_running = True
        self._update_ui_status("Initializing...")

        await self._setup_tools()

        if self._ipc_output:
            self._register_ipc_usage_hook()

        self._agent_task = asyncio.create_task(self.agent.start())

        # Start Command Queue (lane-based task execution)
        self._cq_task = asyncio.create_task(self.command_queue.run())
        logger.info("CommandQueue started.")

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
            return await self.agent.process_message(content=prompt, sender="heartbeat")
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
        if self.command_queue:
            self.command_queue.stop()
        if self.orchestrator:
            self.orchestrator.stop()
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
        for task in (self._cq_task, self._cron_task, self._heartbeat_task):
            if task:
                task.cancel()
        logger.info("Gazer Brain stopped.")
