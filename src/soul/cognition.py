import os
from typing import Optional
from openai import AsyncOpenAI
from soul.core import CognitiveStep, WorkingMemory, MemoryEntry
import logging
import json

logger = logging.getLogger("GazerCognition")

class LLMCognitiveStep(CognitiveStep):
    """LLM-based cognitive step (OpenAI / Ollama / Local compatible)."""
    def __init__(self, name: str, model: str = "gpt-4o", api_key: Optional[str] = None, base_url: Optional[str] = None, default_headers: Optional[dict] = None):
        super().__init__(name)
        self.model = model
        
        # Use "EMPTY" placeholder for local models that don't require a key.
        # Callers should resolve credentials via ModelRegistry before construction.
        self.api_key = api_key or "EMPTY"
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=base_url,
            default_headers=default_headers,
        )
        logger.info("LLM Client initialized. Model: %s, Base: %s", model, base_url or 'Default')

    async def run(self, memory: WorkingMemory, system_prompt: str, tools: list = None, **kwargs) -> MemoryEntry:
        """Run the cognitive step and generate a response."""
        if not self.api_key or self.api_key == "EMPTY":
            return MemoryEntry(sender=memory.owner, content="[System Error: LLM API Key missing]")
        
        messages = [{"role": "system", "content": system_prompt}]
        
        # Convert working memory into an LLM message list
        for m in memory.memories:
            role = "assistant" if m.sender == memory.owner else "user"
            messages.append({"role": role, "content": m.content})

        try:
            # Prepare OpenAI-compatible tool definitions
            openai_tools = []
            if tools:
                for t in tools:
                    if isinstance(t, dict):
                        fn = t.get("function")
                        if t.get("type") == "function" and isinstance(fn, dict):
                            openai_tools.append(t)
                        continue
                    openai_tools.append(
                        {
                            "type": "function",
                            "function": {
                                "name": t.name,
                                "description": t.description,
                                "parameters": t.parameters,
                            },
                        }
                    )

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=openai_tools if openai_tools else None,
                **kwargs
            )
            
            msg = response.choices[0].message
            
            # Handle tool call intents from the model
            if msg.tool_calls:
                tool_calls = []
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.warning("Failed to parse tool call args for %s: %s", tc.function.name, e)
                        args = {}
                    tool_calls.append({"name": tc.function.name, "args": args})
                return MemoryEntry(
                    sender=memory.owner,
                    content=msg.content if msg.content else "",
                    metadata={"tool_calls": tool_calls}
                )

            return MemoryEntry(sender=memory.owner, content=msg.content)
        except Exception as e:
            logger.error("OpenAI API call failed: %s", e)
            return MemoryEntry(sender=memory.owner, content="[System Error: Cognitive Failure]")

    async def process_with_image(self, prompt: str, image_base64: str, system_prompt: str = "You are a helpful assistant.") -> str:
        """Process a vision task.

        :param prompt: The user's question (e.g. "What is on the screen?")
        :param image_base64: Base64-encoded image string (without the data:image/jpeg;base64 prefix)
        :param system_prompt: System prompt for the LLM
        :return: Text response from the model
        """
        if not self.api_key or self.api_key == "EMPTY":
            return "[Error: LLM Client not initialized]"

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            }
                        }
                    ]
                }
            ]

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=300
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error("Vision API call failed: %s", e)
            return f"[Error: Vision Analysis Failed - {str(e)}]"
