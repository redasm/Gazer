"""Gazer Brain -- the central coordinator for perception, cognition and action."""

import logging
import asyncio
import json
from pathlib import Path
import os
from typing import Any, List, Optional

from runtime.config_manager import config, get_config
from runtime.paths import resolve_runtime_root
from perception.audio import get_audio
from hardware import create_body_driver, BodyDriver
from memory import MemoryManager
from memory.openviking_bootstrap import ensure_openviking_ready
from agent.adapter import GazerAgent

from devices.registry import DeviceRegistry
from runtime.app_context import AppContext, set_app_context

from runtime.subsystems.perception import init_capture
from runtime.subsystems.devices import init_devices
from runtime.subsystems.channels import init_channels, init_gmail_push
from runtime.subsystems.tools import setup_tools
from runtime.subsystems.email import init_email_client
from runtime.subsystems.canvas import init_canvas

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
        get_config()  # Fail fast if config is unavailable
        ensure_openviking_ready(config)
        self.app_context = AppContext()
        set_app_context(self.app_context)

        self.memory_manager = MemoryManager()
        self.audio = get_audio()
        self.body: BodyDriver = create_body_driver(config)

        self.spatial = None
        if config.get("perception.spatial_enabled", False):
            from perception.spatial import get_spatial
            self.spatial = get_spatial()

        self.ui_queue = ui_queue
        self.audio.set_queues(ui_queue)
        self.is_running = False
        self._rust_sidecar_client = None

        workspace_path = resolve_runtime_root(config)
        self._sync_soul_single_source(workspace_path)
        self.agent = GazerAgent(workspace_path, self.memory_manager)

        self.app_context.llm_router = self.agent.router
        self.app_context.usage_tracker = self.agent.loop.usage
        self.app_context.prompt_cache_tracker = self.agent.loop.prompt_cache
        self.app_context.tool_batching_tracker = self.agent.loop.tool_batching_tracker
        self.app_context.trajectory_store = self.agent.loop.trajectory_store
        self.app_context.personality = self.agent.personality

        self.canvas_state = init_canvas(
            config, self.app_context, self._get_canvas_on_change(),
        )
        self._email_client = init_email_client(config)

        self.app_context.hook_bus = self.agent.bus
        self.app_context.hook_token = config.get("hooks.token", "") or None

        self.capture_manager = init_capture(config, self.memory_manager)
        self.device_registry = DeviceRegistry(
            default_target=config.get("devices.default_target", "local-desktop"),
        )
        self._rust_sidecar_client = init_devices(
            config, self.device_registry, self.capture_manager,
            self.body, self.spatial,
            self.audio, self.ui_queue, self._rust_sidecar_client,
        )

        self.channels: List[Any] = init_channels(
            config, self.agent.bus, self.ui_queue, self.app_context,
        )
        self._gmail_push_manager = init_gmail_push(
            config, self.agent.bus, self.app_context,
        )

        self.cron_scheduler = None
        self.heartbeat_runner = None
        self._agent_task = None
        self._cron_task = None
        self._heartbeat_task = None

    @staticmethod
    def _get_canvas_on_change():
        from tools.admin_api import _canvas_on_change
        return _canvas_on_change

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
        result = await setup_tools(
            config=config,
            agent=self.agent,
            capture_manager=self.capture_manager,
            device_registry=self.device_registry,
            body=self.body,
            spatial=self.spatial,
            canvas_state=self.canvas_state,
            email_client=self._email_client,
            memory_manager=self.memory_manager,
            app_context=self.app_context,
            rust_sidecar_client=self._rust_sidecar_client,
            cron_run_callback=self._run_cron_job,
        )
        self.hook_registry = result["hook_registry"]
        self.cron_scheduler = result["cron_scheduler"]

        self.plugin_loader = result["plugin_loader"]
        self._rust_sidecar_client = result["rust_sidecar_client"]

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
            if job.delivery_channel and job.delivery_chat_id:
                from bus.events import OutboundMessage
                await self.agent.bus.publish_outbound(OutboundMessage(
                    channel=job.delivery_channel,
                    chat_id=job.delivery_chat_id,
                    content=f"[Cron: {job.name}]\n{result}",
                ))
            return result
        except Exception as exc:
            logger.error("Cron job %s execution error: %s", job.id, exc)
            return f"Error: {exc}"

    async def _run_heartbeat(self, prompt: str) -> Optional[str]:
        """Callback for the heartbeat runner."""
        try:
            return await self.agent.process_auto(content=prompt, sender="heartbeat")
        except Exception as exc:
            logger.error("Heartbeat execution error: %s", exc)
            return f"Error: {exc}"

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    async def start(self):
        logger.info("Gazer Brain starting...")
        self.is_running = True
        self._update_ui_status("Initializing...")

        await self._setup_tools()

        self._api_server_task = asyncio.create_task(self._start_admin_api())
        self._agent_task = asyncio.create_task(self.agent.start())

        if self.cron_scheduler:
            self._cron_task = asyncio.create_task(self.cron_scheduler.start())
            logger.info("CronScheduler started.")

        if config.get("scheduler.heartbeat_enabled", True):
            from scheduler.heartbeat import HeartbeatRunner
            workspace_path = resolve_runtime_root(config)
            self.heartbeat_runner = HeartbeatRunner(
                workspace=workspace_path,
                run_callback=self._run_heartbeat,
                interval_seconds=config.get("scheduler.heartbeat_interval", 300),
            )
            self._heartbeat_task = asyncio.create_task(self.heartbeat_runner.start())
            logger.info("HeartbeatRunner started.")

        channel_tasks = []
        for ch in self.channels:
            task = asyncio.create_task(ch.start())
            channel_tasks.append(task)
            logger.info("Channel '%s' activated.", ch.channel_name)

        if self._gmail_push_manager:
            gmail_ok = await self._gmail_push_manager.setup()
            if gmail_ok:
                logger.info("Gmail Pub/Sub watch registered.")
            else:
                logger.warning("Gmail Pub/Sub setup failed (missing deps or credentials).")

        if self.capture_manager:
            await self.capture_manager.start()

        self._init_hardware()

        if config.get("wake_word.enabled", False):
            self._setup_wake_word()

        self._update_ui_status("Vision Active")

        while self.is_running:
            try:
                if config.check_reload():
                    self.app_context.hook_token = config.get("hooks.token", "") or None

                if self.spatial:
                    attention = self.spatial.get_attention_level()
                    is_present = attention > 0
                else:
                    is_present = False

                if not is_present:
                    self.body.gesture("breathe")

                await asyncio.sleep(0.1)

            except Exception as e:
                logger.error("Brain loop error: %s", e, exc_info=True)
                await asyncio.sleep(1)

        for task in channel_tasks:
            task.cancel()

    def _setup_wake_word(self) -> None:
        """Wire GazerAudio wake word detection to publish InboundMessages."""
        keyword = config.get("wake_word.keyword", "gazer")
        logger.info("Wake word enabled: '%s'", keyword)

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

    def stop(self):
        self.is_running = False
        if self.spatial:
            self.spatial.stop()
        self.body.disconnect()
        if self.capture_manager:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.capture_manager.stop())
            except RuntimeError:
                logger.warning(
                    "Event loop unavailable during capture_manager.stop(); using synchronous cleanup fallback."
                )
                sync_stop = getattr(self.capture_manager, "stop_sync", None)
                release = getattr(self.capture_manager, "release", None)
                close = getattr(self.capture_manager, "close", None)
                for cleaner in (sync_stop, release, close):
                    if not callable(cleaner):
                        continue
                    try:
                        cleaner()
                        break
                    except Exception:
                        logger.warning("Synchronous capture cleanup failed", exc_info=True)
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
