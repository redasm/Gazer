"""Async message queue for decoupled channel-agent communication."""

import asyncio
from typing import Callable, Awaitable, List, Dict, Optional
import logging

from bus.events import InboundMessage, OutboundMessage, TypingEvent
from bus.send_policy import SendPolicy

logger = logging.getLogger("MessageBus")

class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.
    
    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.

    An optional ``send_policy`` can be provided to filter outbound messages
    before they reach channel subscribers.  Messages resolved as ``"deny"``
    are silently dropped (with a DEBUG-level log entry).
    """
    
    def __init__(self, send_policy: Optional[SendPolicy] = None):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self._outbound_subscribers: Dict[str, List[Callable[[OutboundMessage], Awaitable[None]]]] = {}
        self._typing_subscribers: Dict[str, List[Callable[[TypingEvent], Awaitable[None]]]] = {}
        self._running = False
        self._send_policy: Optional[SendPolicy] = send_policy
    
    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent."""
        await self.inbound.put(msg)
    
    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()
    
    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        await self.outbound.put(msg)
    
    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()
    
    def subscribe_outbound(
        self, 
        channel: str, 
        callback: Callable[[OutboundMessage], Awaitable[None]]
    ) -> None:
        """Subscribe to outbound messages for a specific channel."""
        if channel not in self._outbound_subscribers:
            self._outbound_subscribers[channel] = []
        self._outbound_subscribers[channel].append(callback)

    def subscribe_typing(
        self,
        channel: str,
        callback: Callable[[TypingEvent], Awaitable[None]],
    ) -> None:
        """Subscribe to typing indicator events for a channel."""
        if channel not in self._typing_subscribers:
            self._typing_subscribers[channel] = []
        self._typing_subscribers[channel].append(callback)

    async def publish_typing(self, event: TypingEvent) -> None:
        """Emit a typing indicator event to subscribed channels."""
        for cb in self._typing_subscribers.get(event.channel, []):
            try:
                await cb(event)
            except Exception as e:
                logger.debug("Typing indicator callback error: %s", e)
    
    async def dispatch_outbound(self) -> None:  # noqa: C901
        """
        Dispatch outbound messages to subscribed channels.
        Run this as a background task.
        """
        self._running = True
        logger.info("MessageBus dispatcher started")
        while self._running:
            try:
                # Use wait_for to allow stopping the loop properly if valid
                try:
                    msg = await asyncio.wait_for(self.outbound.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                    
                # Send-policy gate
                if self._send_policy is not None:
                    _verdict = self._send_policy.resolve(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                    )
                    if _verdict == "deny":
                        logger.debug(
                            "SendPolicy denied outbound: channel=%s chat_id=%s",
                            msg.channel, msg.chat_id,
                        )
                        continue

                subscribers = self._outbound_subscribers.get(msg.channel, [])
                if not subscribers:
                    logger.warning(
                        "No outbound subscribers for channel=%s chat_id=%s (registered channels: %s)",
                        msg.channel,
                        msg.chat_id,
                        list(self._outbound_subscribers.keys()),
                    )
                    
                for callback in subscribers:
                    await self._dispatch_with_retry(callback, msg)
            except Exception as e:
                logger.error("MessageBus dispatch error: %s", e)
                await asyncio.sleep(1)
    
    async def _dispatch_with_retry(
        self,
        callback: Callable[[OutboundMessage], Awaitable[None]],
        msg: OutboundMessage,
        max_retries: int = 3,
    ) -> None:
        """Dispatch with exponential backoff retry."""
        for attempt in range(max_retries):
            try:
                await callback(msg)
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    delay = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(
                        "Outbound dispatch to %s failed (attempt %s), retrying in %ss: %s",
                        msg.channel, attempt + 1, delay, e,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "Outbound dispatch to %s failed after %s attempts: %s",
                        msg.channel, max_retries, e,
                    )

    def stop(self) -> None:
        """Stop the dispatcher loop."""
        self._running = False
    
    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()
    
    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()
