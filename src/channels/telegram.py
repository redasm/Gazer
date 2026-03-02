"""Telegram channel adapter -- routes messages through the MessageBus.

Integrates with the DM-pairing system (inspired by OpenClaw) so that
unknown users are challenged with a code before they can interact.
"""

import asyncio
import logging
from typing import List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

from agent.channel_command_registry import parse_channel_command
from bus.events import OutboundMessage, TypingEvent
from channels.base import ChannelAdapter
from channels.media_utils import save_media, cleanup_old_media
from runtime.config_manager import config
from security.pairing import pairing_manager
from soul.evolution import evolution

logger = logging.getLogger("TelegramChannel")


class TelegramChannel(ChannelAdapter):
    """Telegram bot channel using python-telegram-bot."""

    channel_name = "telegram"

    def __init__(self, token: str, allowed_ids: List[str]) -> None:
        super().__init__()
        self.token = token
        self.allowed_ids = [str(uid) for uid in allowed_ids]
        self.app = ApplicationBuilder().token(token).build()
        self._setup_handlers()

        # Seed pairing manager with pre-configured allowed IDs
        for uid in self.allowed_ids:
            if uid:
                pairing_manager.add_approved("telegram", uid)

    # ------------------------------------------------------------------
    # ChannelAdapter interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        try:
            await self.app.initialize()
            await self.app.start()
            # Test bot token validity
            bot_info = await self.app.bot.get_me()
            logger.info(f"Telegram bot connected: @{bot_info.username} ({bot_info.first_name})")
            await self.app.updater.start_polling()
            logger.info("Telegram polling started.")
        except Exception as exc:
            logger.error(f"Failed to start Telegram channel: {exc}", exc_info=True)
            raise

    async def stop(self) -> None:
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
        logger.info("Telegram stopped.")

    async def send(self, msg: OutboundMessage) -> None:
        try:
            chat_id = int(msg.chat_id)
        except (ValueError, TypeError):
            logger.error(f"Invalid chat_id: {msg.chat_id}")
            return

        if msg.is_partial:
            try:
                await self.app.bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception as exc:
                logger.warning(f"Telegram typing indicator failed: {exc}")
            return

        # --- Send images first (if any) ---
        for media_path in (msg.media or []):
            try:
                from pathlib import Path
                p = Path(media_path)
                if p.is_file():
                    with p.open("rb") as f:
                        await self.app.bot.send_photo(chat_id=chat_id, photo=f)
                    logger.info(f"Telegram sent photo: {media_path}")
            except Exception as exc:
                logger.error(f"Failed to send Telegram photo: {exc}")

        # --- Send text ---
        if not msg.content or not msg.content.strip():
            return

        keyboard = [
            [
                InlineKeyboardButton("\U0001f44d", callback_data="feedback_positive"),
                InlineKeyboardButton("\U0001f44e", callback_data="feedback_negative"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await self.app.bot.send_message(
                chat_id=chat_id, text=msg.content, reply_markup=reply_markup
            )
        except Exception as exc:
            logger.error(f"Failed to send Telegram message to {chat_id}: {exc}")

    async def _on_typing(self, event: TypingEvent) -> None:
        if not event.is_typing:
            return
        try:
            chat_id = int(event.chat_id)
        except (ValueError, TypeError):
            logger.debug("Telegram typing skipped due to invalid chat_id: %s", event.chat_id)
            return
        try:
            await self.app.bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception as exc:
            logger.debug("Telegram typing indicator failed: %s", exc)

    # ------------------------------------------------------------------
    # Inbound handlers
    # ------------------------------------------------------------------

    def _setup_handlers(self) -> None:
        self.app.add_handler(CommandHandler("start", self._on_start))
        self.app.add_handler(CommandHandler("fix", self._on_fix))
        self.app.add_handler(
            MessageHandler(filters.COMMAND, self._on_command_passthrough)
        )
        self.app.add_handler(
            MessageHandler(filters.PHOTO, self._on_photo)
        )
        self.app.add_handler(
            MessageHandler(filters.Document.IMAGE, self._on_document_image)
        )
        self.app.add_handler(
            MessageHandler(filters.VOICE, self._on_voice)
        )
        self.app.add_handler(
            MessageHandler(filters.VIDEO, self._on_video)
        )
        self.app.add_handler(
            MessageHandler(filters.Document.ALL & (~filters.Document.IMAGE), self._on_document_file)
        )
        self.app.add_handler(
            MessageHandler(filters.TEXT & (~filters.COMMAND), self._on_message)
        )
        self.app.add_handler(CallbackQueryHandler(self._on_callback))

    async def _on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user:
            logger.warning("Received /start command but update.effective_user is None")
            return
        user_id = str(update.effective_user.id)
        chat_id = str(update.effective_chat.id)
        logger.info(f"Telegram /start received from user {user_id} in chat {chat_id}")
        
        # Check authorization first
        if not self._is_sender_authorized(user_id):
            logger.info(f"User {user_id} is not authorized, issuing pairing challenge")
            await self._on_pairing_challenge(chat_id, user_id)
            return
        
        # Send greeting through AgentLoop for SOUL/memory-powered response
        logger.info(f"User {user_id} authorized, routing /start to AgentLoop")
        await self.publish(
            content="Hello",  # Natural greeting for the agent to respond to
            chat_id=chat_id,
            sender_id=user_id,
        )

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user:
            return
        user_id = str(update.effective_user.id)

        text = update.message.text
        logger.info(f"Telegram received from {user_id}: {text}")

        # publish() enforces DM policy internally (pairing / allowlist / open)
        await self.publish(
            content=text,
            chat_id=str(update.effective_chat.id),
            sender_id=user_id,
        )

    async def _on_command_passthrough(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not update.effective_user:
            return
        if not update.message:
            return

        text = str(update.message.text or "").strip()
        if not text:
            return
        # Keep dedicated Telegram command handlers for /start and /fix.
        head = text.split(maxsplit=1)[0].lower()
        if head.startswith("/start") or head.startswith("/fix"):
            return
        if parse_channel_command(text) is None:
            return

        user_id = str(update.effective_user.id)
        logger.info("Telegram command passthrough from %s: %s", user_id, text)
        await self.publish(
            content=text,
            chat_id=str(update.effective_chat.id),
            sender_id=user_id,
        )

    async def _on_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle photo messages — download and forward with media path."""
        if not update.effective_user:
            return
        user_id = str(update.effective_user.id)
        chat_id = str(update.effective_chat.id)

        # Get highest resolution photo
        photo = update.message.photo[-1]
        caption = update.message.caption or "[User sent an image]"

        try:
            tg_file = await photo.get_file()
            data = await tg_file.download_as_bytearray()
            path = save_media(bytes(data), ext=".jpg", prefix="tg")
            logger.info(f"Telegram photo from {user_id}: {path} ({len(data)} bytes)")
            await self.publish(
                content=caption,
                chat_id=chat_id,
                sender_id=user_id,
                media=[str(path)],
            )
        except Exception as exc:
            logger.error(f"Failed to download Telegram photo: {exc}", exc_info=True)
            await self.publish(content=caption, chat_id=chat_id, sender_id=user_id)

    async def _on_document_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle image documents (uncompressed images sent as files)."""
        if not update.effective_user:
            return
        user_id = str(update.effective_user.id)
        chat_id = str(update.effective_chat.id)

        doc = update.message.document
        caption = update.message.caption or "[User sent an image]"
        # Determine extension from mime type
        ext = ".jpg"
        if doc.mime_type:
            ext_map = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
            ext = ext_map.get(doc.mime_type, ".jpg")

        try:
            tg_file = await doc.get_file()
            data = await tg_file.download_as_bytearray()
            path = save_media(bytes(data), ext=ext, prefix="tg")
            logger.info(f"Telegram document image from {user_id}: {path} ({len(data)} bytes)")
            await self.publish(
                content=caption,
                chat_id=chat_id,
                sender_id=user_id,
                media=[str(path)],
            )
        except Exception as exc:
            logger.error(f"Failed to download Telegram document image: {exc}", exc_info=True)
            await self.publish(content=caption, chat_id=chat_id, sender_id=user_id)

    async def _on_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle Telegram voice messages and forward as media."""
        if not update.effective_user:
            return
        if not update.message or not update.message.voice:
            return
        user_id = str(update.effective_user.id)
        chat_id = str(update.effective_chat.id)
        voice = update.message.voice
        caption = update.message.caption or "[User sent a voice message]"
        ext = ".ogg"
        if voice.mime_type == "audio/mpeg":
            ext = ".mp3"
        elif voice.mime_type == "audio/wav":
            ext = ".wav"
        try:
            tg_file = await voice.get_file()
            data = await tg_file.download_as_bytearray()
            path = save_media(bytes(data), ext=ext, prefix="tg_voice")
            logger.info(f"Telegram voice from {user_id}: {path} ({len(data)} bytes)")
            await self.publish(
                content=caption,
                chat_id=chat_id,
                sender_id=user_id,
                media=[str(path)],
                metadata={
                    "telegram_message_type": "voice",
                    "telegram_mime_type": voice.mime_type or "",
                    "telegram_duration_seconds": int(getattr(voice, "duration", 0) or 0),
                },
            )
        except Exception as exc:
            logger.error(f"Failed to download Telegram voice: {exc}", exc_info=True)
            await self.publish(content=caption, chat_id=chat_id, sender_id=user_id)

    async def _on_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle Telegram video messages and forward as media."""
        if not update.effective_user:
            return
        if not update.message or not update.message.video:
            return
        user_id = str(update.effective_user.id)
        chat_id = str(update.effective_chat.id)
        video = update.message.video
        caption = update.message.caption or "[User sent a video]"
        ext = ".mp4"
        if video.mime_type:
            if "webm" in video.mime_type:
                ext = ".webm"
            elif "quicktime" in video.mime_type:
                ext = ".mov"
        try:
            tg_file = await video.get_file()
            data = await tg_file.download_as_bytearray()
            path = save_media(bytes(data), ext=ext, prefix="tg_video")
            logger.info(f"Telegram video from {user_id}: {path} ({len(data)} bytes)")
            await self.publish(
                content=caption,
                chat_id=chat_id,
                sender_id=user_id,
                media=[str(path)],
                metadata={
                    "telegram_message_type": "video",
                    "telegram_mime_type": video.mime_type or "",
                    "telegram_duration_seconds": int(getattr(video, "duration", 0) or 0),
                },
            )
        except Exception as exc:
            logger.error(f"Failed to download Telegram video: {exc}", exc_info=True)
            await self.publish(content=caption, chat_id=chat_id, sender_id=user_id)

    async def _on_document_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle non-image document files and forward as media."""
        if not update.effective_user:
            return
        if not update.message or not update.message.document:
            return
        user_id = str(update.effective_user.id)
        chat_id = str(update.effective_chat.id)
        doc = update.message.document
        caption = update.message.caption or "[User sent a file]"
        ext = ".bin"
        if doc.file_name and "." in doc.file_name:
            ext = "." + doc.file_name.rsplit(".", 1)[-1].lower()
        elif doc.mime_type:
            mime_map = {
                "application/pdf": ".pdf",
                "text/plain": ".txt",
                "application/zip": ".zip",
                "audio/mpeg": ".mp3",
                "video/mp4": ".mp4",
            }
            ext = mime_map.get(doc.mime_type, ".bin")
        try:
            tg_file = await doc.get_file()
            data = await tg_file.download_as_bytearray()
            path = save_media(bytes(data), ext=ext, prefix="tg_file")
            logger.info(f"Telegram file from {user_id}: {path} ({len(data)} bytes)")
            await self.publish(
                content=caption,
                chat_id=chat_id,
                sender_id=user_id,
                media=[str(path)],
                metadata={
                    "telegram_message_type": "file",
                    "telegram_mime_type": doc.mime_type or "",
                    "telegram_file_name": doc.file_name or "",
                },
            )
        except Exception as exc:
            logger.error(f"Failed to download Telegram file: {exc}", exc_info=True)
            await self.publish(content=caption, chat_id=chat_id, sender_id=user_id)

    async def _on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user:
            return
        query = update.callback_query
        await query.answer()

        label = "positive" if "positive" in query.data else "negative"
        evolution.collect_feedback(label, "telegram_reply", feedback_text=f"User clicked {label}")

        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"Thanks for your {label} feedback! Gazer will keep evolving."
        )

    async def _on_fix(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.message.reply_text("Please provide improvement suggestions, e.g.: /fix be more concise.")
            return

        feedback_text = " ".join(context.args)
        evolution.collect_feedback("correction", "telegram_command", feedback_text=feedback_text)
        await update.message.reply_text("Feedback received! Gazer will evolve based on your suggestions.")
