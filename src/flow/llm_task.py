"""LLM Task step — structured LLM reasoning within a workflow.

Aligned with Lobster's ``llm-task`` plugin: lets deterministic pipelines
insert an LLM call that returns structured JSON output.
"""

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("LLMTask")


class LLMTaskStep:
    """Execute a structured LLM reasoning step.

    Usage within FlowEngine: when ``step.tool == "llm_task"``, the engine
    delegates to this class instead of the tool registry.
    """

    def __init__(self, provider: Any) -> None:
        """*provider* must be an :class:`LLMProvider` instance."""
        self._provider = provider

    async def execute(
        self,
        prompt: str,
        input_data: Any = None,
        schema: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
    ) -> Any:
        """Call the LLM with *prompt* + *input_data* and return parsed JSON.

        Args:
            prompt: Instruction for the LLM.
            input_data: Data to include in the prompt (will be JSON-serialized).
            schema: Optional JSON Schema for output validation.
            model: Optional model override.

        Returns:
            Parsed JSON output from the LLM.

        Raises:
            ValueError: If the LLM output is not valid JSON or fails schema validation.
        """
        # Build the user message
        parts = [prompt]
        if input_data is not None:
            serialized = json.dumps(input_data, ensure_ascii=False, default=str)
            parts.append(f"\nInput:\n```json\n{serialized}\n```")
        if schema:
            parts.append(
                f"\nOutput must conform to this JSON Schema:\n"
                f"```json\n{json.dumps(schema, ensure_ascii=False)}\n```"
            )
        parts.append("\nRespond with valid JSON only, no explanation.")

        user_content = "\n".join(parts)
        messages = [
            {"role": "system", "content": "You are a data processing assistant. Always respond with valid JSON."},
            {"role": "user", "content": user_content},
        ]

        response = await self._provider.chat(
            messages=messages,
            model=model,
            temperature=0.1,  # Low temp for structured output
            max_tokens=4096,
        )

        if response.error or not response.content:
            raise ValueError(f"LLM call failed: {response.content or 'empty response'}")

        # Parse JSON from response (handle markdown code fences)
        text = response.content.strip()
        if text.startswith("```"):
            # Strip markdown code fences
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM output is not valid JSON: {exc}\nRaw: {text[:500]}")

        # Basic schema validation (type check on root)
        if schema:
            self._validate_root(parsed, schema)

        return parsed

    @staticmethod
    def _validate_root(data: Any, schema: Dict[str, Any]) -> None:
        """Minimal root-level type validation."""
        expected_type = schema.get("type")
        if expected_type == "array" and not isinstance(data, list):
            raise ValueError(f"Expected array, got {type(data).__name__}")
        if expected_type == "object" and not isinstance(data, dict):
            raise ValueError(f"Expected object, got {type(data).__name__}")
        if expected_type == "string" and not isinstance(data, str):
            raise ValueError(f"Expected string, got {type(data).__name__}")
