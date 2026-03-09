"""Base class for all message channel adapters.

Includes DM-policy enforcement inspired by OpenClaw's pairing model:
before publishing inbound messages, the adapter checks whether the
sender is authorized under the configured ``dm_policy``.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional, Dict, Type, Callable

from bus.events import InboundMessage, OutboundMessage, TypingEvent
from bus.queue import MessageBus
from runtime.config_manager import config
from security.owner import get_owner_manager
from security.pairing import get_pairing_manager

# Session reset trigger words (inspired by OpenClaw /new /reset)
_SESSION_RESET_TRIGGERS = {"/new", "/reset"}

logger = logging.getLogger("ChannelAdapter")


class ChannelAdapter(ABC):
    """
    Unified interface for message channels (Telegram, Web, Discord, ...).

    All inbound messages MUST go through ``publish()`` -> MessageBus.
    All outbound messages arrive via ``send()`` from the Bus dispatcher.

    DM Policy (``security.dm_policy``) -- inspired by OpenClaw:
      * ``"open"``      -- all senders are accepted (default for dev).
      * ``"allowlist"``  -- only senders in channel-specific allow lists.
      * ``"pairing"``    -- unknown senders receive a pairing code that
                           an admin must approve before the sender can interact.
    """

    channel_name: str = ""

    def __init__(self) -> None:
        self.bus: Optional[MessageBus] = None

    @classmethod
    def from_config(cls, config: Any, **kwargs: Any) -> Optional["ChannelAdapter"]:
        """
        Factory method to create a channel instance from the central configuration.
        Returns None if disabled or missing required credentials.
        """
        raise NotImplementedError("Each ChannelAdapter must implement from_config.")

    def bind(self, bus: MessageBus) -> None:
        """Bind this adapter to a MessageBus (subscribe outbound automatically)."""
        self.bus = bus
        bus.subscribe_outbound(self.channel_name, self.send)
        bus.subscribe_typing(self.channel_name, self._on_typing)
        logger.info("Channel '%s' bound to MessageBus.", self.channel_name)

    @abstractmethod
    async def start(self) -> None:
        """Start listening / polling. Called once after bind()."""

    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown."""

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """Deliver an outbound message to this channel's transport."""

    # ------------------------------------------------------------------
    # Typing indicators (subclasses can override for platform-specific API)
    # ------------------------------------------------------------------

    async def _on_typing(self, event: TypingEvent) -> None:
        """Handle typing indicator events.

        Subclasses should override to call platform-specific "typing" APIs.
        Default is a no-op.
        """
        pass

    # ------------------------------------------------------------------
    # DM-policy hook (subclasses can override ``_on_pairing_challenge``)
    # ------------------------------------------------------------------

    def _get_dm_policy(self) -> str:
        """Return the DM policy for this channel."""
        # Per-channel override, falls back to global setting
        return config.get(
            f"{self.channel_name}.dm_policy",
            config.get("security.dm_policy", "open"),
        )

    def _is_sender_authorized(self, sender_id: str) -> bool:
        """Check whether *sender_id* is authorized to send messages.

        Returns ``True`` if the sender can proceed.
        The **Owner** always bypasses all DM policies.
        """
        # Owner always has access
        if get_owner_manager().is_owner_sender(self.channel_name, sender_id):
            return True

        policy = self._get_dm_policy()

        if policy == "open":
            return True

        # Both "allowlist" and "pairing" require the sender to be approved
        return get_pairing_manager().is_approved(self.channel_name, sender_id)

    async def _on_pairing_challenge(self, chat_id: str, sender_id: str) -> None:
        """Called when an unapproved sender needs a pairing code.

        Default implementation generates a code and sends it as an outbound
        message.  Subclasses may override for channel-specific formatting.
        """
        code = get_pairing_manager().challenge(self.channel_name, sender_id)
        try:
            await self.send(
                OutboundMessage(
                    channel=self.channel_name,
                    chat_id=chat_id,
                    content=(
                        f"🔐 Please provide this pairing code to the admin for access: {code}"
                    ),
                )
            )
        except Exception as exc:
            logger.warning("Failed to send pairing challenge: %s", exc)

    async def publish(
        self,
        content: str,
        chat_id: str,
        sender_id: str,
        media: Optional[list] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Convenience: build an InboundMessage and publish to the Bus.

        Enforces DM policy before forwarding to the bus.
        """
        if not self.bus:
            logger.warning("Channel '%s' not bound to any bus.", self.channel_name)
            return

        # --- DM policy enforcement ---
        policy = self._get_dm_policy()
        if not self._is_sender_authorized(sender_id):
            if policy == "pairing":
                await self._on_pairing_challenge(chat_id, sender_id)
            else:
                logger.info("Sender %s blocked by DM policy '%s' on %s", sender_id, policy, self.channel_name)
            return

        # --- Session reset triggers ---
        stripped = content.strip().lower()
        reset_word = stripped.split()[0] if stripped else ""
        if reset_word in _SESSION_RESET_TRIGGERS:
            # Ask the bus to reset (metadata flag picked up by AgentLoop)
            remainder = content.strip()[len(reset_word):].strip()
            await self.bus.publish_inbound(
                InboundMessage(
                    channel=self.channel_name,
                    chat_id=chat_id,
                    sender_id=sender_id,
                    content=remainder or "Hello!",
                    media=media or [],
                    metadata={**(metadata or {}), "_reset_session": True},
                )
            )
            return

        await self.bus.publish_inbound(
            InboundMessage(
                channel=self.channel_name,
                chat_id=chat_id,
                sender_id=sender_id,
                content=content,
                media=media or [],
                metadata=metadata or {},
            )
        )


class ChannelRegistry:
    """Registry for ChannelAdapter implementations."""

    _registry: Dict[str, Type[ChannelAdapter]] = {}

    @classmethod
    def register(cls, name: str) -> Callable[[Type[ChannelAdapter]], Type[ChannelAdapter]]:
        """Decorator to register a ChannelAdapter specific class against a channel name."""
        def wrapper(channel_cls: Type[ChannelAdapter]) -> Type[ChannelAdapter]:
            cls._registry[name] = channel_cls
            return channel_cls
        return wrapper

    @classmethod
    def get_all(cls) -> Dict[str, Type[ChannelAdapter]]:
        """Returned all registered channels."""
        return cls._registry.copy()
