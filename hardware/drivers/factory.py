"""Factory for creating BodyDriver instances from config."""

import logging
from typing import TYPE_CHECKING

from hardware.drivers.base import BodyDriver, NullDriver

if TYPE_CHECKING:
    from runtime.config_manager import ConfigManager

logger = logging.getLogger("GazerHardware")


def create_body_driver(config: "ConfigManager") -> BodyDriver:
    """Instantiate the appropriate BodyDriver based on ``body.type`` config.

    Supported types:
    - ``"none"`` → NullDriver (no hardware, all operations are no-ops)
    - ``"serial_arm"`` → SerialArmDriver (USB serial robotic arm)

    Lazy-imports concrete drivers so their dependencies (e.g. pyserial)
    are only required when actually selected.
    """
    body_type = config.get("body.type", "none")

    if body_type == "none":
        logger.info("Body type is 'none' — using NullDriver.")
        return NullDriver()

    if body_type == "serial_arm":
        from hardware.drivers.serial_arm import SerialArmDriver

        port = config.get("body.port", "auto")
        baudrate = config.get("body.baudrate", 115200)
        logger.info(f"Body type is 'serial_arm' — port={port}, baudrate={baudrate}")
        return SerialArmDriver(port=port, baudrate=baudrate)

    raise ValueError(
        f"Unknown body type: {body_type!r}. "
        f"Supported: none, serial_arm"
    )
