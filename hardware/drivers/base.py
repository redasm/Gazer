"""Hardware Abstraction Layer -- abstract BodyDriver and NullDriver."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class BodyDriver(ABC):
    """Abstract interface for any physical body (arm, rover, quadruped, none).

    Every hardware body must implement this interface so the rest of the
    system (Brain, tools, gestures) can interact with it uniformly.
    """

    @abstractmethod
    def connect(self) -> bool:
        """Attempt to connect to the hardware. Return True on success."""

    @abstractmethod
    def disconnect(self) -> None:
        """Gracefully disconnect from the hardware."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the driver is currently connected and functional."""

    @abstractmethod
    def gesture(self, name: str, **kwargs) -> None:
        """Execute a named gesture (e.g. 'nod', 'shake_head', 'breathe').

        Each driver maps gesture names to its own physical movements.
        Unknown gesture names should be silently ignored.
        """

    @abstractmethod
    def set_actuator(self, name: str, value: Any) -> None:
        """Low-level actuator control (e.g. set a servo angle)."""

    @abstractmethod
    def set_leds(self, rgb: List[int]) -> None:
        """Set LED colour as [R, G, B] (0-255 each)."""

    @property
    def capabilities(self) -> List[str]:
        """Return the list of gesture names this driver supports."""
        return []


class NullDriver(BodyDriver):
    """No-op driver for running without any physical hardware."""

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        pass

    @property
    def is_connected(self) -> bool:
        return True

    def gesture(self, name: str, **kwargs) -> None:
        pass

    def set_actuator(self, name: str, value: Any) -> None:
        pass

    def set_leds(self, rgb: List[int]) -> None:
        pass

    @property
    def capabilities(self) -> List[str]:
        return []
