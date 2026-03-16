"""Outbound send-policy filter for the MessageBus.

A ``SendPolicy`` is a lightweight rule engine that decides whether an
outbound message should be *allowed* or *denied* before it reaches the
subscribed channel callbacks.

Rules are evaluated in order; the **first match** wins.  If no rule
matches, the ``default`` action is applied (``"allow"`` unless configured
otherwise).

Rule fields
-----------
``action``         -- ``"allow"`` or ``"deny"``
``channel``        -- exact channel name to match, or ``""`` to match all
``chat_id_prefix`` -- match chat IDs that *start with* this string, or ``""``
                      to match all
``sender_id``      -- exact sender ID to match, or ``""`` to match all

Example usage
-------------
::

    from bus.send_policy import SendPolicy, SendPolicyRule

    policy = SendPolicy(
        rules=[
            # Block all traffic to the "spam" channel
            SendPolicyRule(action="deny", channel="spam"),
            # Allow only chat IDs that start with "admin_" on the support channel
            SendPolicyRule(action="allow", channel="support", chat_id_prefix="admin_"),
            SendPolicyRule(action="deny", channel="support"),
        ],
        default="allow",
    )

    # Attach to the bus
    bus = MessageBus(send_policy=policy)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SendPolicyRule:
    """A single rule in a ``SendPolicy``.

    All non-empty fields must match for the rule to apply.
    Empty string means "match anything".
    """

    action: str                  # "allow" | "deny"
    channel: str = ""            # Exact channel name; "" = any
    chat_id_prefix: str = ""     # chat_id must start with this; "" = any
    sender_id: str = ""          # Exact sender_id; "" = any

    def matches(
        self,
        channel: str,
        chat_id: str,
        sender_id: str = "",
    ) -> bool:
        """Return True if this rule matches the given outbound coordinates."""
        if self.channel and self.channel != channel:
            return False
        if self.chat_id_prefix and not chat_id.startswith(self.chat_id_prefix):
            return False
        if self.sender_id and self.sender_id != sender_id:
            return False
        return True


@dataclass
class SendPolicy:
    """Ordered list of ``SendPolicyRule`` objects with a fallback default.

    Evaluation is first-match-wins.  If no rule matches, ``default`` is used.
    """

    rules: List[SendPolicyRule] = field(default_factory=list)
    default: str = "allow"       # "allow" | "deny"

    def resolve(
        self,
        channel: str,
        chat_id: str,
        sender_id: str = "",
    ) -> str:
        """Return ``"allow"`` or ``"deny"`` for the given outbound message.

        Iterates rules in order; returns the action of the first matching rule.
        Falls back to ``self.default`` if no rule matches.
        """
        for rule in self.rules:
            if rule.matches(channel, chat_id, sender_id):
                return rule.action
        return self.default
