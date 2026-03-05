"""System tools: time, image analysis, and other utilities."""

import datetime
import logging
import os
from typing import Any, Dict, Optional

from tools.base import Tool

logger = logging.getLogger("SystemTools")


class SystemToolBase(Tool):
    @property
    def provider(self) -> str:
        return "system"

    @staticmethod
    def _error(code: str, message: str) -> str:
        return f"Error [{code}]: {message}"


class GetTimeTool(SystemToolBase):
    """Return the current date and time."""

    @property
    def name(self) -> str:
        return "get_time"


    @property
    def description(self) -> str:
        return "Get the current date and time."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **_: Any) -> str:
        now = datetime.datetime.now()
        return now.strftime("%Y-%m-%d %H:%M:%S (%A)")


class ImageAnalyzeTool(SystemToolBase):
    """Analyze an image file using a vision language model."""

    @property
    def name(self) -> str:
        return "image_analyze"


    @property
    def description(self) -> str:
        return "Analyze an image file using a vision model. Describe contents or answer a question about the image."

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "image_path": {
                    "type": "string",
                    "description": "Path to the image file.",
                },
                "prompt": {
                    "type": "string",
                    "description": "Question or instruction about the image. Defaults to 'Describe this image.'",
                },
            },
            "required": ["image_path"],
        }

    async def execute(self, image_path: str, prompt: str = "Describe this image.", **_: Any) -> str:
        if not os.path.isfile(image_path):
            return self._error("SYSTEM_IMAGE_NOT_FOUND", f"file '{image_path}' not found.")

        # Validate that image_path is within the workspace or an absolute known path
        from pathlib import Path
        workspace = Path(os.getcwd()).resolve()
        resolved = Path(image_path).resolve()
        if not (resolved == workspace or resolved.is_relative_to(workspace)):
            return self._error(
                "SYSTEM_IMAGE_PATH_OUTSIDE_WORKSPACE",
                "image_path must be within the workspace directory.",
            )

        try:
            import base64
            from PIL import Image
            import io

            img = Image.open(image_path)
            img.thumbnail((1280, 720))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        except ImportError:
            return self._error("SYSTEM_DEPENDENCY_MISSING", "Pillow is not installed.")
        except Exception as exc:
            return self._error("SYSTEM_IMAGE_READ_FAILED", f"Error reading image: {exc}")

        try:
            from soul.cognition import LLMCognitiveStep
            from soul.models import ModelRegistry

            api_key, base_url, model_name, headers = ModelRegistry.resolve_model("fast_brain")
            vlm = LLMCognitiveStep(
                name="ImageAnalyze",
                model=model_name,
                api_key=api_key,
                base_url=base_url,
                default_headers=headers,
            )
            result = await vlm.process_with_image(
                prompt=prompt,
                image_base64=b64,
                system_prompt="You are a helpful image analysis assistant. Be concise.",
            )
            return result.strip()
        except Exception as exc:
            return self._error("SYSTEM_IMAGE_ANALYZE_FAILED", f"Error analyzing image: {exc}")
