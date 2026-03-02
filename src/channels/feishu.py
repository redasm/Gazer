"""Feishu / Lark channel adapter -- routes messages through the MessageBus.

Uses the lark-oapi SDK's WebSocket long-connection mode so the channel
runs entirely inside the Brain process (no HTTP webhook required).

Integrates with the DM-pairing system so that unknown users are
challenged with a code before they can interact.
"""

import asyncio
import base64
import json
import logging
import os
import tempfile
import threading
import time
from collections import deque
from typing import Any, List, Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateFileRequest,
    CreateFileRequestBody,
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    DeleteMessageRequest,
    GetMessageResourceRequest,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from agent.channel_command_registry import parse_channel_command
from bus.events import OutboundMessage, TypingEvent
from channels.base import ChannelAdapter
from channels.media_utils import save_media
from runtime.config_manager import config
from security.pairing import pairing_manager

logger = logging.getLogger("FeishuChannel")


def _parse_text_content(raw_content: str, message_type: str) -> str:
    """Extract plain text from Feishu's JSON-wrapped message content."""
    try:
        parsed = json.loads(raw_content)
    except (json.JSONDecodeError, TypeError):
        return raw_content

    if message_type == "text":
        return parsed.get("text", raw_content)

    if message_type == "post":
        # Rich-text: walk content blocks and concatenate text elements
        title = parsed.get("title", "")
        blocks = parsed.get("content", [])
        parts = [title] if title else []
        for paragraph in blocks:
            if not isinstance(paragraph, list):
                continue
            line_parts = []
            for elem in paragraph:
                if elem.get("tag") == "text":
                    line_parts.append(elem.get("text", ""))
                elif elem.get("tag") == "a":
                    line_parts.append(elem.get("text", elem.get("href", "")))
                elif elem.get("tag") == "at":
                    line_parts.append(f"@{elem.get('user_name', '')}")
            parts.append("".join(line_parts))
        return "\n".join(parts).strip() or "[Rich text message]"

    if message_type in {"image", "file", "audio", "video", "sticker"}:
        # Parse non-text payload metadata so inbound context is not a blind placeholder.
        detail_keys = {
            "image": ["image_key"],
            "file": ["file_name", "file_key"],
            "audio": ["file_name", "duration", "file_key", "audio_key"],
            "video": ["file_name", "duration", "file_key", "video_key"],
            "sticker": ["sticker_key", "file_key", "image_key"],
        }.get(message_type, [])
        details: list[str] = []
        for key in detail_keys:
            value = parsed.get(key)
            if value in (None, ""):
                continue
            details.append(f"{key}={value}")
        prefix = f"[{message_type}]"
        return f"{prefix} {', '.join(details[:3])}".strip()

    return f"[{message_type}]"


class FeishuChannel(ChannelAdapter):
    """Feishu bot channel using lark-oapi WebSocket mode."""

    channel_name = "feishu"

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        allowed_ids: Optional[List[str]] = None,
    ) -> None:
        super().__init__()
        self.app_id = app_id
        self.app_secret = app_secret
        self.allowed_ids = [str(uid) for uid in (allowed_ids or [])]

        # API client for sending messages
        self.client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws_client: Optional[lark.ws.Client] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._seen_message_ids: deque[str] = deque(maxlen=512)
        self._seen_message_id_set: set[str] = set()
        self._typing_last_sent_at: dict[str, float] = {}
        self._typing_status_message_ids: dict[str, str] = {}

        # Seed pairing manager with pre-configured allowed IDs
        for uid in self.allowed_ids:
            if uid:
                pairing_manager.add_approved("feishu", uid)

    # ------------------------------------------------------------------
    # ChannelAdapter interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message_receive)
            .build()
        )

        self._ws_client = lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        # ws.Client.start() is blocking and calls run_until_complete(),
        # so it needs its own event loop in a dedicated thread.
        self._ws_thread = threading.Thread(
            target=self._run_ws,
            name="feishu-ws",
            daemon=True,
        )
        self._ws_thread.start()
        logger.info("Feishu WebSocket client started.")

    _MAX_RETRIES = 5
    _RETRY_BASE_DELAY = 5  # seconds

    def _run_ws(self) -> None:
        """Entry point for the feishu-ws thread with its own event loop.

        The lark-oapi SDK captures ``asyncio.get_event_loop()`` into a
        **module-level** ``loop`` variable at import time.  When the main
        asyncio loop is already running this causes ``RuntimeError: This
        event loop is already running``.  We work around it by replacing
        that module-level variable with a fresh loop created in this
        dedicated thread.

        Implements exponential back-off retry on connection failure.
        """
        import time as _time
        import lark_oapi.ws.client as _ws_mod
        from lark_oapi.ws.exception import ClientException

        for attempt in range(1, self._MAX_RETRIES + 1):
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            _ws_mod.loop = new_loop  # override SDK's module-level loop

            try:
                self._ws_client.start()
                return  # clean exit
            except ClientException as exc:
                # Credential errors won't be fixed by retrying
                logger.error(
                    "Feishu connection failed (attempt %d/%d): %s — "
                    "please check app_id / app_secret in settings.",
                    attempt, self._MAX_RETRIES, exc,
                )
                new_loop.close()
                if "invalid" in str(exc).lower():
                    logger.warning(
                        "Feishu channel disabled due to invalid credentials. "
                        "Fix config and restart."
                    )
                    return
            except Exception:
                logger.exception(
                    "Feishu WebSocket error (attempt %d/%d)",
                    attempt, self._MAX_RETRIES,
                )
                new_loop.close()

            delay = self._RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.info("Feishu reconnecting in %ds...", delay)
            _time.sleep(delay)

        logger.error(
            "Feishu channel gave up after %d attempts.", self._MAX_RETRIES
        )

    async def stop(self) -> None:
        # The SDK ws client doesn't expose a clean stop API;
        # the daemon thread will be killed when the process exits.
        logger.info("Feishu channel stopped.")

    async def send(self, msg: OutboundMessage) -> None:
        chat_id = msg.chat_id
        if not chat_id:
            logger.error("Feishu send: missing chat_id")
            return

        if msg.is_partial:
            # Feishu doesn't have a native "typing" indicator API for bots
            return

        # --- Send media files (images or documents) ---
        if msg.media:
            logger.info(f"Feishu send: {len(msg.media)} media file(s) to send")
        for media_path in (msg.media or []):
            self._send_media_file(chat_id, media_path)

        # --- Send text ---
        if not msg.content or not msg.content.strip():
            return

        sent_ok, _ = self._send_text_message(chat_id=chat_id, text=msg.content, context="message")
        if not sent_ok:
            return
        self._cleanup_typing_status_message(chat_id)

    def _send_text_message(self, *, chat_id: str, text: str, context: str) -> tuple[bool, str]:
        content = json.dumps({"text": text})

        try:
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = self.client.im.v1.message.create(request)
            if not response.success():
                logger.error(
                    "Feishu %s send failed: code=%s, msg=%s",
                    context,
                    response.code,
                    response.msg,
                )
                return False, ""
            message_id = ""
            data = getattr(response, "data", None)
            if data is not None:
                message_id = str(getattr(data, "message_id", "") or "").strip()
            return True, message_id
        except Exception as exc:
            logger.error("Feishu %s send exception: %s", context, exc, exc_info=True)
            return False, ""

    def _cleanup_typing_status_message(self, chat_id: str) -> None:
        enabled = bool(config.get("feishu.simulated_typing.auto_recall_on_reply", True))
        if not enabled:
            return
        status_message_id = self._typing_status_message_ids.pop(chat_id, "")
        if not status_message_id:
            return
        try:
            request = DeleteMessageRequest.builder().message_id(status_message_id).build()
            response = self.client.im.v1.message.delete(request)
            if not response.success():
                logger.debug(
                    "Feishu typing auto-recall failed: code=%s, msg=%s, message_id=%s",
                    response.code,
                    response.msg,
                    status_message_id,
                )
        except Exception as exc:
            logger.debug("Feishu typing auto-recall exception: %s", exc)

    async def _on_typing(self, event: TypingEvent) -> None:
        if not event.is_typing:
            return
        chat_id = str(event.chat_id or "").strip()
        if not chat_id:
            return

        enabled = bool(config.get("feishu.simulated_typing.enabled", False))
        if not enabled:
            return

        text = str(config.get("feishu.simulated_typing.text", "正在思考中...") or "").strip()
        if not text:
            return

        min_interval_raw = config.get("feishu.simulated_typing.min_interval_seconds", 8)
        try:
            min_interval = max(1.0, float(min_interval_raw))
        except (TypeError, ValueError):
            min_interval = 8.0

        now = time.monotonic()
        last_sent_at = self._typing_last_sent_at.get(chat_id, 0.0)
        if now - last_sent_at < min_interval:
            return

        sent_ok, message_id = self._send_text_message(chat_id=chat_id, text=text, context="typing")
        if sent_ok:
            self._typing_last_sent_at[chat_id] = now
            if message_id:
                self._typing_status_message_ids[chat_id] = message_id

    # Image file extensions (case-insensitive)
    _IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
    _MEDIA_RESOURCE_SPECS: dict[str, dict[str, Any]] = {
        "image": {
            "keys": ["image_key"],
            "resource_type": "image",
            "resource_type_fallbacks": [],
            "ext": ".png",
            "fallback_text": "[User sent an image]",
        },
        "file": {
            "keys": ["file_key"],
            "resource_type": "file",
            "resource_type_fallbacks": [],
            "ext": ".bin",
            "fallback_text": "[User sent a file]",
        },
        "audio": {
            "keys": ["file_key", "audio_key"],
            "resource_type": "file",
            "resource_type_fallbacks": ["audio"],
            "ext": ".mp3",
            "fallback_text": "[User sent an audio clip]",
        },
        "video": {
            "keys": ["file_key", "video_key"],
            "resource_type": "video",
            "resource_type_fallbacks": ["file"],
            "ext": ".mp4",
            "fallback_text": "[User sent a video]",
        },
        "sticker": {
            "keys": ["image_key", "sticker_key", "file_key"],
            "resource_type": "image",
            "resource_type_fallbacks": ["file"],
            "ext": ".png",
            "fallback_text": "[User sent a sticker]",
        },
    }
    _media_whisper_model: Any = None

    def _send_media_file(self, chat_id: str, file_path: str) -> None:
        """Upload and send a file (image or document) to Feishu."""
        from pathlib import Path
        import io
        
        p = Path(file_path)
        if not p.is_file():
            logger.warning(f"Feishu: file not found: {file_path}")
            return

        # Determine if this is an image or generic file
        ext = p.suffix.lower()
        is_image = ext in self._IMAGE_EXTENSIONS
        
        logger.info(f"Feishu: sending {'image' if is_image else 'file'} to {chat_id}: {file_path}")
        
        try:
            file_data = p.read_bytes()
            logger.info(f"Feishu: read {len(file_data)} bytes from {file_path}")
            
            if is_image:
                self._send_image(chat_id, file_data, file_path)
            else:
                self._send_file(chat_id, file_data, p.name, file_path)
        except Exception as exc:
            logger.error(f"Feishu send media exception: {exc}", exc_info=True)

    def _send_image(self, chat_id: str, image_data: bytes, file_path: str) -> None:
        """Upload and send an image."""
        import io
        
        # Step 1: Upload image to get image_key
        upload_req = (
            CreateImageRequest.builder()
            .request_body(
                CreateImageRequestBody.builder()
                .image_type("message")
                .image(io.BytesIO(image_data))
                .build()
            )
            .build()
        )
        upload_resp = self.client.im.v1.image.create(upload_req)
        if not upload_resp.success():
            logger.error(
                f"Feishu image upload failed: code={upload_resp.code}, msg={upload_resp.msg}"
            )
            return
        image_key = upload_resp.data.image_key
        logger.info(f"Feishu: image uploaded, key={image_key}")

        # Step 2: Send image message
        content = json.dumps({"image_key": image_key})
        send_req = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("image")
                .content(content)
                .build()
            )
            .build()
        )
        send_resp = self.client.im.v1.message.create(send_req)
        if not send_resp.success():
            logger.error(
                f"Feishu image send failed: code={send_resp.code}, msg={send_resp.msg}"
            )
        else:
            logger.info(f"Feishu sent image: {file_path}")

    def _send_file(self, chat_id: str, file_data: bytes, file_name: str, file_path: str) -> None:
        """Upload and send a generic file."""
        import io
        
        # Step 1: Upload file to get file_key
        upload_req = (
            CreateFileRequest.builder()
            .request_body(
                CreateFileRequestBody.builder()
                .file_type("stream")
                .file_name(file_name)
                .file(io.BytesIO(file_data))
                .build()
            )
            .build()
        )
        upload_resp = self.client.im.v1.file.create(upload_req)
        if not upload_resp.success():
            logger.error(
                f"Feishu file upload failed: code={upload_resp.code}, msg={upload_resp.msg}"
            )
            return
        file_key = upload_resp.data.file_key
        logger.info(f"Feishu: file uploaded, key={file_key}")

        # Step 2: Send file message
        content = json.dumps({"file_key": file_key})
        send_req = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("file")
                .content(content)
                .build()
            )
            .build()
        )
        send_resp = self.client.im.v1.message.create(send_req)
        if not send_resp.success():
            logger.error(
                f"Feishu file send failed: code={send_resp.code}, msg={send_resp.msg}"
            )
        else:
            logger.info(f"Feishu sent file: {file_path}")

    # ------------------------------------------------------------------
    # Inbound event handler (called from SDK WebSocket thread)
    # ------------------------------------------------------------------

    def _on_message_receive(self, data: lark.im.v1.P2ImMessageReceiveV1) -> None:
        """Handle im.message.receive_v1 event from the Feishu SDK.

        This runs in the WebSocket thread, so we bridge to the async
        event loop via ``run_coroutine_threadsafe``.
        """
        if not self._loop:
            logger.warning("Feishu: event received before start()")
            return

        event = data.event
        if not event or not event.message or not event.sender:
            return

        message = event.message
        sender = event.sender

        sender_id = ""
        if sender.sender_id:
            sender_id = sender.sender_id.open_id or sender.sender_id.user_id or ""
        if not sender_id:
            logger.warning("Feishu: message with no sender_id, skipping")
            return
        sender_type = str(getattr(sender, "sender_type", "") or "").strip().lower()

        chat_id = message.chat_id or ""
        chat_type = message.chat_type or "p2p"
        message_type = message.message_type or "text"
        raw_content = message.content or ""
        message_id = message.message_id or ""

        if sender_type == "app":
            logger.debug("Feishu: ignore app-sender message id=%s sender=%s", message_id, sender_id)
            return
        if message_id and not self._remember_message_id(message_id):
            logger.debug("Feishu: ignore duplicate message id=%s", message_id)
            return

        # --- Media download ---
        media_paths: list[str] = []
        inbound_metadata: dict[str, Any] = {
            "feishu_message_id": message_id,
            "feishu_message_type": message_type,
        }
        media_records: list[dict[str, str]] = []
        if message_id and message_type in self._MEDIA_RESOURCE_SPECS:
            media_path = self._download_message_resource(
                message_id=message_id,
                raw_content=raw_content,
                message_type=message_type,
            )
            if media_path:
                media_paths.append(media_path)
                media_records.append({"path": media_path, "message_type": message_type})
        if media_records:
            inbound_metadata["feishu_media"] = media_records

        text = _parse_text_content(raw_content, message_type)
        if not text and not media_paths:
            return
        # If only media with no text, provide a default prompt.
        if not text and media_paths:
            text = self._media_fallback_text(message_type)

        logger.info(
            f"Feishu received from {sender_id} in {chat_id} ({chat_type}): {text[:80]}"
        )

        # For group messages, check if bot was @mentioned
        # (simple implementation: always process in p2p, require mention in group)
        if chat_type == "group":
            mentions = message.mentions or []
            # If no mentions at all, skip (group privacy)
            if not mentions:
                logger.debug(f"Feishu: group message without mention, skipping")
                return
            # Strip @bot mention text from the content
            for mention in mentions:
                if hasattr(mention, "name") and mention.name:
                    text = text.replace(f"@{mention.name}", "").strip()
                if hasattr(mention, "key") and mention.key:
                    text = text.replace(mention.key, "").strip()
            if not text:
                return

        # Use sender's open_id as both sender_id and chat_id for DM;
        # for groups, use the group chat_id
        publish_chat_id = chat_id if chat_type == "group" else sender_id

        future = asyncio.run_coroutine_threadsafe(
            self._handle_inbound(
                text,
                publish_chat_id,
                sender_id,
                media_paths,
                inbound_metadata,
            ),
            self._loop,
        )
        # Log any exceptions from the coroutine
        future.add_done_callback(self._log_future_error)

    @classmethod
    def _media_fallback_text(cls, message_type: str) -> str:
        spec = cls._MEDIA_RESOURCE_SPECS.get(message_type, {})
        return str(spec.get("fallback_text", f"[User sent a {message_type}]"))

    @classmethod
    def _extract_resource_descriptor(
        cls,
        raw_content: str,
        message_type: str,
    ) -> tuple[str, str, str, list[str]]:
        spec = cls._MEDIA_RESOURCE_SPECS.get(message_type) or {}
        keys = spec.get("keys", [])
        if not isinstance(keys, list) or not keys:
            return "", "", "", []

        try:
            parsed = json.loads(raw_content)
        except (json.JSONDecodeError, TypeError):
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}

        file_key = ""
        for key_name in keys:
            candidate = str(parsed.get(key_name, "") or "").strip()
            if candidate:
                file_key = candidate
                break

        resource_type = str(spec.get("resource_type", "") or "").strip()
        ext = str(spec.get("ext", "") or "").strip()
        fallback_types = spec.get("resource_type_fallbacks", [])
        if not isinstance(fallback_types, list):
            fallback_types = []
        normalized_fallbacks = [str(item).strip() for item in fallback_types if str(item).strip()]
        return file_key, resource_type, ext, normalized_fallbacks

    def _download_message_resource(self, message_id: str, raw_content: str, message_type: str) -> Optional[str]:
        """Download media from a Feishu message and return the local file path."""
        file_key, resource_type, ext, fallback_types = self._extract_resource_descriptor(
            raw_content=raw_content,
            message_type=message_type,
        )
        if not file_key:
            logger.warning("Feishu %s message has no resource key", message_type)
            return None
        if not resource_type:
            logger.warning("Feishu %s message has no resource type mapping", message_type)
            return None

        try:
            for candidate_type in [resource_type, *fallback_types]:
                request = (
                    GetMessageResourceRequest.builder()
                    .message_id(message_id)
                    .file_key(file_key)
                    .type(candidate_type)
                    .build()
                )
                response = self.client.im.v1.message_resource.get(request)
                if not response.success():
                    logger.warning(
                        "Feishu %s download failed with type=%s: code=%s, msg=%s",
                        message_type,
                        candidate_type,
                        response.code,
                        response.msg,
                    )
                    continue

                data = response.file.read()
                if not data:
                    logger.warning("Feishu %s download returned empty data", message_type)
                    return None

                save_ext = ext or ".bin"
                path = save_media(data, ext=save_ext, prefix=f"feishu_{message_type}")
                logger.info(
                    "Feishu %s downloaded: %s (%d bytes, type=%s)",
                    message_type,
                    path,
                    len(data),
                    candidate_type,
                )
                return str(path)
            return None
        except Exception as exc:
            logger.error("Feishu %s download exception: %s", message_type, exc, exc_info=True)
            return None

    async def _handle_inbound(
        self, text: str, chat_id: str, sender_id: str,
        media: Optional[list] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Process an inbound message on the async event loop."""
        if media:
            text, metadata = await self._augment_media_context(
                text=text,
                media=media,
                metadata=metadata,
            )

        # Check if user needs pairing first (for /start command)
        if text.strip().lower() in ("/start", "start") and not media:
            if not self._is_sender_authorized(sender_id):
                logger.info(f"Feishu: user {sender_id} not authorized, issuing pairing")
                await self._on_pairing_challenge(chat_id, sender_id)
                return
            # If authorized, let the message flow through to AgentLoop
            # so the agent can respond with its personality

        publish_metadata = dict(metadata or {})
        parsed_command = parse_channel_command(text)
        if parsed_command is not None:
            command_name, command_args = parsed_command
            publish_metadata["channel_command"] = {
                "name": command_name,
                "args": command_args,
            }

        # All messages (including greetings) go through AgentLoop for
        # proper SOUL/memory-powered responses
        await self.publish(
            content=text,
            chat_id=chat_id,
            sender_id=sender_id,
            media=media,
            metadata=publish_metadata,
        )

    async def _augment_media_context(
        self,
        text: str,
        media: Optional[list],
        metadata: Optional[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]]:
        result_text = str(text or "").strip()
        payload = dict(metadata or {})
        cfg = config.get("feishu.media_analysis", {}) or {}
        if not isinstance(cfg, dict) or not bool(cfg.get("enabled", True)):
            return result_text, payload

        media_records = payload.get("feishu_media", [])
        if not isinstance(media_records, list):
            media_records = []
        if not media:
            return result_text, payload

        analyses: list[dict[str, str]] = []
        for idx, media_path in enumerate(media):
            msg_type = "file"
            if idx < len(media_records):
                msg_type = str(media_records[idx].get("message_type", "file") or "file")
            analysis = await self._analyze_media_file(str(media_path), msg_type, cfg)
            if analysis:
                analyses.append(
                    {"path": str(media_path), "message_type": msg_type, "analysis": analysis}
                )

        if analyses and bool(cfg.get("include_inbound_summary", True)):
            lines = ["[Feishu media analysis]"]
            for item in analyses:
                lines.append(f"- {item['message_type']}: {item['analysis']}")
            summary = "\n".join(lines)
            result_text = f"{result_text}\n\n{summary}".strip()
            payload["feishu_media_analysis"] = analyses
        return result_text, payload

    async def _analyze_media_file(self, path: str, message_type: str, cfg: dict) -> str:
        p = str(path or "").strip()
        if not p or not os.path.isfile(p):
            return ""
        if message_type in {"image", "sticker"} and bool(cfg.get("analyze_images", True)):
            return await self._analyze_image_file(p, cfg)
        if message_type == "audio" and bool(cfg.get("transcribe_audio", True)):
            return self._transcribe_audio_file(p, cfg)
        if message_type == "video" and bool(cfg.get("analyze_video_keyframe", True)):
            return await self._analyze_video_file(p, cfg)
        return self._summarize_file(p)

    @staticmethod
    def _summarize_file(path: str) -> str:
        try:
            size = os.path.getsize(path)
            name = os.path.basename(path)
            ext = os.path.splitext(name)[1].lower()
            return f"name={name}, ext={ext or 'n/a'}, size={size} bytes"
        except OSError:
            return f"name={os.path.basename(path)}"

    async def _analyze_image_file(self, path: str, cfg: dict) -> str:
        try:
            from PIL import Image
            from soul.cognition import LLMCognitiveStep
            from soul.models import ModelRegistry
        except Exception:
            return self._summarize_file(path)

        try:
            with Image.open(path) as img:
                img.thumbnail((1280, 720))
                width, height = img.size
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                tmp_path = tmp.name
                tmp.close()
                img.save(tmp_path, format="JPEG", quality=80)
            with open(tmp_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            os.remove(tmp_path)
        except Exception:
            return self._summarize_file(path)

        try:
            api_key, base_url, model_name, headers = ModelRegistry.resolve_model("fast_brain")
            step = LLMCognitiveStep(
                name="FeishuMediaAnalyze",
                model=model_name,
                api_key=api_key,
                base_url=base_url,
                default_headers=headers,
            )
            timeout_seconds = float(cfg.get("timeout_seconds", 12) or 12)
            prompt = "Describe key visual content and any visible text in one short sentence."
            result = await asyncio.wait_for(
                step.process_with_image(
                    prompt=prompt,
                    image_base64=b64,
                    system_prompt="Return concise plain text. Keep under 120 characters.",
                ),
                timeout=timeout_seconds,
            )
            summary = str(result or "").strip()
            if summary:
                return f"{summary} (resolution={width}x{height})"
        except Exception:
            pass
        return self._summarize_file(path)

    async def _analyze_video_file(self, path: str, cfg: dict) -> str:
        try:
            import cv2
        except Exception:
            return self._summarize_file(path)

        temp_frame = ""
        try:
            cap = cv2.VideoCapture(path)
            ok, frame = cap.read()
            cap.release()
            if not ok or frame is None:
                return self._summarize_file(path)
            fd, temp_frame = tempfile.mkstemp(prefix="feishu_video_", suffix=".jpg")
            os.close(fd)
            cv2.imwrite(temp_frame, frame)
            image_summary = await self._analyze_image_file(temp_frame, cfg)
            return f"keyframe: {image_summary}"
        except Exception:
            return self._summarize_file(path)
        finally:
            if temp_frame and os.path.exists(temp_frame):
                try:
                    os.remove(temp_frame)
                except OSError:
                    pass

    @classmethod
    def _get_media_whisper_model(cls, model_size: str):
        if cls._media_whisper_model is not None:
            return cls._media_whisper_model
        try:
            from faster_whisper import WhisperModel

            cls._media_whisper_model = WhisperModel(model_size, device="cpu", compute_type="int8")
            return cls._media_whisper_model
        except Exception:
            return None

    def _transcribe_audio_file(self, path: str, cfg: dict) -> str:
        model_size = str(cfg.get("audio_whisper_model", "base") or "base").strip()
        model = self._get_media_whisper_model(model_size)
        if model is None:
            return self._summarize_file(path)
        try:
            segments, _ = model.transcribe(path, beam_size=3)
            text = "".join([segment.text for segment in segments]).strip()
            if text:
                return f"transcript={text[:240]}"
        except Exception:
            return self._summarize_file(path)
        return self._summarize_file(path)

    @staticmethod
    def _log_future_error(future: asyncio.Future) -> None:
        exc = future.exception()
        if exc:
            logger.error(f"Feishu inbound handler error: {exc}", exc_info=exc)

    def _remember_message_id(self, message_id: str) -> bool:
        """Record Feishu message_id and return False for duplicates."""
        key = str(message_id or "").strip()
        if not key:
            return True
        if key in self._seen_message_id_set:
            return False
        if len(self._seen_message_ids) == self._seen_message_ids.maxlen:
            oldest = self._seen_message_ids.popleft()
            self._seen_message_id_set.discard(oldest)
        self._seen_message_ids.append(key)
        self._seen_message_id_set.add(key)
        return True
