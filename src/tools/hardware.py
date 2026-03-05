"""Hardware and Vision tools for Gazer."""

import asyncio
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from tools.base import Tool
from hardware.drivers.base import BodyDriver

if TYPE_CHECKING:
    from perception.spatial import SpatialPerceiver


class HardwareToolBase(Tool):
    @property
    def provider(self) -> str:
        return "hardware"

    @staticmethod
    def _error(code: str, message: str) -> str:
        return f"Error [{code}]: {message}"


class HardwareControlTool(HardwareToolBase):
    """Tool for controlling Gazer's hardware (Servos, LEDs)."""
    
    def __init__(self, body: BodyDriver):
        self.body = body

    @property
    def name(self) -> str:
        return "hardware_control"

    @property
    def owner_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return "Control Gazer's physical body, including head movements and LED lights."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string", 
                    "enum": ["move_head", "set_led"],
                    "description": "The action to perform."
                },
                "yaw": {
                    "type": "integer", 
                    "minimum": 0, 
                    "maximum": 180,
                    "description": "Head yaw angle (0-180), for move_head action."
                },
                "pitch": {
                    "type": "integer", 
                    "minimum": 0, 
                    "maximum": 180,
                    "description": "Head pitch angle (0-180), for move_head action."
                },
                "rgb": {
                    "type": "array", 
                    "items": {"type": "integer"}, 
                    "minItems": 3, 
                    "maxItems": 3,
                    "description": "RGB color values as list of 3 integers (0-255), for set_led action."
                }
            },
            "required": ["action"]
        }

    async def execute(self, action: str, yaw: Optional[int] = None, pitch: Optional[int] = None, rgb: Optional[List[int]] = None, **kwargs) -> str:
        loop = asyncio.get_running_loop()
        if action == "move_head":
            angles = {}
            if yaw is not None:
                angles["head_yaw"] = yaw
            if pitch is not None:
                angles["head_pitch"] = pitch
            
            if not angles:
                return self._error("HARDWARE_MOVE_HEAD_ARGS_REQUIRED", "No angles provided for move_head.")

            for name, value in angles.items():
                await loop.run_in_executor(None, self.body.set_actuator, name, value)
            return f"Head moved to {angles}"
            
        elif action == "set_led":
            if not rgb:
                return self._error("HARDWARE_SET_LED_ARGS_REQUIRED", "No RGB values provided for set_led.")
            
            await loop.run_in_executor(None, self.body.set_leds, rgb)
            return f"LEDs set to RGB {rgb}"
            
        return self._error("HARDWARE_ACTION_UNKNOWN", f"Unknown action {action}")


class VisionTool(HardwareToolBase):
    """Tool for querying visual/spatial information."""
    
    def __init__(self, spatial: Optional["SpatialPerceiver"] = None):
        self.spatial = spatial

    @property
    def name(self) -> str:
        return "vision_query"


    @property
    def description(self) -> str:
        return "Get information about the visual environment, such as user presence and distance."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query_type": {
                    "type": "string", 
                    "enum": ["user_status", "distance", "attention"],
                    "description": "The type of information to query."
                }
            },
            "required": ["query_type"]
        }

    async def execute(self, query_type: str, **kwargs) -> str:
        if not self.spatial:
            return "Vision/spatial perception is not enabled."
        loop = asyncio.get_running_loop()
        if query_type == "distance":
            dist = await loop.run_in_executor(None, self.spatial.get_user_distance)
            return f"User distance: {dist:.2f} meters" if dist is not None else "User distance: Unknown"
            
        elif query_type == "attention":
            attn = await loop.run_in_executor(None, self.spatial.get_attention_level)
            return f"User attention level: {attn:.2f} (0-1)"
            
        elif query_type == "user_status":
            # Combine simple metrics
            dist = await loop.run_in_executor(None, self.spatial.get_user_distance)
            attn = await loop.run_in_executor(None, self.spatial.get_attention_level)
            zone = await loop.run_in_executor(None, self.spatial.get_interaction_zone)
            
            status = "User is present." if attn > 0 else "No user detected."
            details = f"Distance: {dist}, Zone: {zone}, Attention: {attn}"
            return f"{status} {details}"
            
        return self._error("HARDWARE_QUERY_UNKNOWN", f"Unknown query type {query_type}")
