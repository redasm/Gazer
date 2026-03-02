"""LiteLLM provider implementation for multi-provider support."""

import asyncio
import os
import json
import logging
import random
import re
from urllib.parse import urlparse
from typing import Any, AsyncIterator, List, Dict, Optional, Tuple, Type

try:
    import litellm
    from litellm import acompletion
    try:
        from litellm import aresponses  # type: ignore
    except Exception:
        aresponses = None
    from litellm.exceptions import (
        RateLimitError,
        ServiceUnavailableError,
        APIConnectionError,
        Timeout,
    )
    _LITELLM_RETRYABLE_ERRORS: Tuple[Type[Exception], ...] = (
        APIConnectionError,
        Timeout,
    )
except ImportError:
    litellm = None
    acompletion = None
    aresponses = None
    _LITELLM_RETRYABLE_ERRORS = ()

logger = logging.getLogger("LiteLLMProvider")

# Retry configuration defaults
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BASE_DELAY = 1.0  # seconds
_DEFAULT_MAX_DELAY = 30.0  # seconds
_DEFAULT_JITTER = 0.5  # random factor
_DEFAULT_REQUEST_TIMEOUT = 60.0  # seconds
_OPENAI_COMPAT_API_MODES = {
    "openai",
    "openai-responses",
    "responses",
    "openai_response",
    "openai-completions",
    "chat-completions",
}

from llm.base import LLMProvider, LLMResponse, ToolCallRequest

class LiteLLMProvider(LLMProvider):
    """
    LLM provider using LiteLLM for multi-provider support.
     Supports OpenRouter, Anthropic, OpenAI, Gemini, and many other providers through
    a unified interface.
    """
    
    def __init__(
        self, 
        api_key: Optional[str] = None, 
        api_base: Optional[str] = None,
        default_model: str = "openrouter/anthropic/claude-3-opus",  # Reasonable default
        api_mode: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        model_settings: Optional[Dict[str, Dict[str, Any]]] = None,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_base_delay: float = _DEFAULT_BASE_DELAY,
        retry_max_delay: float = _DEFAULT_MAX_DELAY,
        request_timeout: float = _DEFAULT_REQUEST_TIMEOUT,
        auth_mode: Optional[str] = None,
        auth_header: bool = False,
        strict_api_mode: bool = True,
        reasoning_param: Optional[bool] = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.api_mode = str(api_mode or "").strip().lower()
        self.extra_headers = dict(extra_headers or {})
        self.model_settings = dict(model_settings or {})
        normalized_auth_mode = str(auth_mode or "").strip().lower()
        if normalized_auth_mode not in {"", "api-key", "bearer", "none"}:
            normalized_auth_mode = ""
        self.auth_mode = normalized_auth_mode
        if self.auth_mode == "none":
            self.auth_header = False
        elif self.auth_mode in {"api-key", "bearer"}:
            self.auth_header = True
        else:
            self.auth_header = bool(auth_header)
        self.strict_api_mode = bool(strict_api_mode)
        self.reasoning_param = bool(reasoning_param) if isinstance(reasoning_param, bool) else None
        
        # Retry configuration
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.retry_max_delay = retry_max_delay
        self.request_timeout = request_timeout if request_timeout > 0 else _DEFAULT_REQUEST_TIMEOUT
        
        if not litellm:
            raise ImportError("litellm is required for LiteLLMProvider")

        # Detect OpenRouter by api_key prefix or explicit api_base
        self.is_openrouter = (
            (api_key and api_key.startswith("sk-or-")) or
            (api_base and "openrouter" in api_base)
        )
        
        # Disable LiteLLM logging noise
        litellm.suppress_debug_info = True

    def _resolve_model_settings(self, model: str) -> Dict[str, Any]:
        if not self.model_settings:
            return {}
        model_key = str(model or "").strip()
        bare = self._normalize_model_name(model_key)
        return (
            self.model_settings.get(model_key)
            or self.model_settings.get(bare)
            or {}
        )

    @staticmethod
    def _extract_input_capabilities(settings: Dict[str, Any]) -> Optional[set[str]]:
        raw = settings.get("input")
        if not isinstance(raw, list):
            return None
        allowed = {str(item).strip().lower() for item in raw if str(item).strip()}
        return allowed or None

    @staticmethod
    def _detect_message_modalities(messages: List[Dict[str, Any]]) -> set[str]:
        used: set[str] = set()
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    ptype = str(part.get("type") or "").lower()
                    if ptype in {"image_url", "input_image"}:
                        used.add("image")
                    elif ptype in {"text", "input_text", "output_text"}:
                        used.add("text")
            else:
                used.add("text")
        return used or {"text"}

    def get_model_context_window(self, model: Optional[str] = None) -> Optional[int]:
        settings = self._resolve_model_settings(model or self.default_model)
        raw = settings.get("contextWindow") or settings.get("context_window")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def get_model_cost(self, model: Optional[str] = None) -> Optional[Dict[str, float]]:
        settings = self._resolve_model_settings(model or self.default_model)
        raw = settings.get("cost")
        if not isinstance(raw, dict):
            return None
        out: Dict[str, float] = {}
        for key in ("input", "output", "cacheRead", "cacheWrite"):
            try:
                out[key] = float(raw.get(key, 0))
            except (TypeError, ValueError):
                out[key] = 0.0
        return out
    
    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff delay with jitter.
        
        Uses: delay = min(base * 2^attempt + jitter, max_delay)
        """
        delay = self.retry_base_delay * (2 ** attempt)
        delay = min(delay, self.retry_max_delay)
        # Add random jitter to prevent thundering herd
        jitter = random.uniform(0, _DEFAULT_JITTER * delay)
        return delay + jitter
    
    def _is_retryable_error(self, error: Exception) -> bool:
        """Check if an error should trigger a retry."""
        error_str = str(error).lower()
        if any(kw in error_str for kw in ("rate limit", "ratelimit", "429")):
            return False
        if self._is_gateway_error(error):
            return True
        if _LITELLM_RETRYABLE_ERRORS and isinstance(error, _LITELLM_RETRYABLE_ERRORS):
            return True
        # Also retry on generic connection/timeout errors
        retryable_keywords = (
            "timeout", "timed out",
            "connection", "connect error",
            "connection reset", "connection aborted",
            "network",
        )
        return any(kw in error_str for kw in retryable_keywords)

    @staticmethod
    def _is_gateway_error(error: Exception) -> bool:
        error_str = str(error).lower()
        gateway_keywords = (
            "bad gateway",
            "502",
            "service unavailable",
            "503",
            "upstream host returned an html error page",
            "cloudflare ray id",
        )
        return any(kw in error_str for kw in gateway_keywords)

    @staticmethod
    def _summarize_error(error: Optional[Exception]) -> str:
        raw = str(error or "unknown error").strip()
        if not raw:
            return "unknown error"

        lowered = raw.lower()
        if "<!doctype html" in lowered or "<html" in lowered:
            title_match = re.search(r"<title>(.*?)</title>", raw, flags=re.IGNORECASE | re.DOTALL)
            title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else "Upstream gateway error"
            ray_match = re.search(r"Cloudflare Ray ID:\s*<strong[^>]*>([^<]+)</strong>", raw, flags=re.IGNORECASE)
            ray = ray_match.group(1).strip() if ray_match else ""
            if ray:
                return f"{title}. Upstream host returned an HTML error page (Ray ID: {ray})."
            return f"{title}. Upstream host returned an HTML error page."

        # Keep message compact for UI/chat readability.
        raw = re.sub(r"\s+", " ", raw)
        if len(raw) > 320:
            return raw[:320].rstrip() + "..."
        return raw

    @staticmethod
    def _normalize_model_name(model: str) -> str:
        if "/" not in model:
            return model
        return model.split("/")[-1]

    def _should_use_responses_api(self, model: str) -> bool:
        _ = model
        # Keep routing deterministic: only explicit provider/model config should
        # select Responses API. Avoid model-name heuristics like "gpt-5*".
        return self.api_mode in {"openai-responses", "responses", "openai_response"}

    def _resolve_custom_llm_provider(self) -> Optional[str]:
        # Hint LiteLLM provider resolution for custom OpenAI-compatible gateways.
        if self.is_openrouter:
            return None
        if not self.api_base:
            return None
        if self.api_mode:
            return "openai" if self.api_mode in _OPENAI_COMPAT_API_MODES else None
        # Backward-compatible fallback for legacy configs without explicit api mode.
        return "openai"

    @staticmethod
    def _is_official_openai_base(api_base: Optional[str]) -> bool:
        """Return True for official OpenAI hosts (or unset base_url)."""
        if not api_base:
            return True
        try:
            host = (urlparse(str(api_base)).hostname or "").strip().lower()
        except Exception:
            return False
        if not host:
            return False
        return host == "api.openai.com" or host.endswith(".openai.com")

    @staticmethod
    def _resolve_reasoning_supported(settings: Dict[str, Any]) -> Optional[bool]:
        for key in ("reasoning_supported", "reasoningSupported", "reasoning"):
            if key not in settings:
                continue
            raw = settings.get(key)
            if isinstance(raw, bool):
                return raw
        return None

    def _should_send_reasoning(
        self,
        *,
        reasoning_pref: Any,
        reasoning_supported: Optional[bool],
        use_responses_api: bool,
        model: str,
    ) -> bool:
        """Gate reasoning param to avoid gateway incompatibilities."""
        if reasoning_pref is not True or not use_responses_api:
            return False
        if self.reasoning_param is not None:
            return self.reasoning_param
        if reasoning_supported is not None:
            return reasoning_supported
        if self.strict_api_mode:
            logger.warning(
                "Skipping reasoning param because capability is unspecified in strict mode: model=%s api_base=%s",
                model,
                self.api_base,
            )
            return False
        if self._is_official_openai_base(self.api_base):
            return True
        logger.warning(
            "Skipping reasoning param for non-OpenAI-compatible gateway (compat fallback mode): model=%s api_base=%s",
            model,
            self.api_base,
        )
        return False

    def _build_request_headers(self) -> Dict[str, str]:
        headers = dict(self.extra_headers or {})
        if self.auth_mode == "none":
            return headers
        if not self.auth_header:
            return headers
        key = str(self.api_key or "").strip()
        if not key:
            return headers
        has_authorization = any(str(k).strip().lower() == "authorization" for k in headers.keys())
        if not has_authorization:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _messages_to_responses_input(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert chat-completions-style messages to Responses API input format."""
        converted: List[Dict[str, Any]] = []
        for msg in messages:
            role = str(msg.get("role") or "user").strip().lower()
            raw_content = msg.get("content", "")

            if role == "tool":
                call_id = str(msg.get("tool_call_id") or msg.get("call_id") or "").strip()
                if call_id:
                    converted.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": str(raw_content or ""),
                        }
                    )
                else:
                    converted.append(
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": str(raw_content or "")}],
                        }
                    )
                continue

            normalized_role = role if role in {"system", "user", "assistant", "developer"} else "user"
            text_part_type = "output_text" if normalized_role == "assistant" else "input_text"

            if isinstance(raw_content, list):
                parts: List[Dict[str, str]] = []
                for part in raw_content:
                    if not isinstance(part, dict):
                        continue
                    ptype = str(part.get("type") or "").lower()
                    if ptype in {"text", "input_text", "output_text"}:
                        text = part.get("text") or part.get("content") or ""
                        parts.append({"type": text_part_type, "text": str(text)})
                    elif ptype in {"image_url", "input_image"}:
                        if normalized_role == "assistant":
                            continue
                        image_url = part.get("image_url") or part.get("url")
                        if isinstance(image_url, dict):
                            image_url = image_url.get("url")
                        if image_url:
                            parts.append({"type": "input_image", "image_url": str(image_url)})
                if not parts:
                    parts = [{"type": text_part_type, "text": ""}]
            else:
                parts = [{"type": text_part_type, "text": str(raw_content)}]

            converted.append({"role": normalized_role, "content": parts})

            if normalized_role == "assistant":
                tool_calls = msg.get("tool_calls")
                if isinstance(tool_calls, list):
                    for call in tool_calls:
                        if not isinstance(call, dict):
                            continue
                        call_id = str(call.get("id") or "").strip()
                        function = call.get("function") if isinstance(call.get("function"), dict) else {}
                        name = str(function.get("name") or "").strip()
                        arguments = function.get("arguments", "{}")
                        if isinstance(arguments, dict):
                            arguments = json.dumps(arguments, ensure_ascii=False)
                        if call_id and name:
                            converted.append(
                                {
                                    "type": "function_call",
                                    "call_id": call_id,
                                    "name": name,
                                    "arguments": str(arguments),
                                }
                            )
        return converted

    @staticmethod
    def _tools_to_responses_format(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert Chat Completions tool schema to Responses API format.

        Chat Completions: {"type":"function","function":{"name":...,"parameters":...}}
        Responses API:    {"type":"function","name":...,"parameters":...}
        """
        converted: List[Dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            fn = tool.get("function")
            if isinstance(fn, dict) and tool.get("type") == "function":
                converted.append({"type": "function", **fn})
            else:
                converted.append(tool)
        return converted

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        model = model or self.default_model
        selected_settings = self._resolve_model_settings(model)
        allowed_inputs = self._extract_input_capabilities(selected_settings)
        if allowed_inputs is not None:
            used_inputs = self._detect_message_modalities(messages)
            blocked_inputs = sorted(used_inputs - allowed_inputs)
            if blocked_inputs:
                return LLMResponse(
                    content=(
                        f"Configured model input types {sorted(allowed_inputs)} do not allow "
                        f"message types {blocked_inputs}."
                    ),
                    finish_reason="error",
                    error=True,
                    model=model,
                )
        raw_model_max_tokens = selected_settings.get("maxTokens") or selected_settings.get("max_tokens")
        try:
            model_max_tokens = int(raw_model_max_tokens) if raw_model_max_tokens is not None else None
        except (TypeError, ValueError):
            model_max_tokens = None
        if model_max_tokens and model_max_tokens > 0:
            max_tokens = min(max_tokens, model_max_tokens)
        reasoning_pref = selected_settings.get("reasoning")
        reasoning_supported = self._resolve_reasoning_supported(selected_settings)
        use_responses_api = self._should_use_responses_api(model)
        send_reasoning = self._should_send_reasoning(
            reasoning_pref=reasoning_pref,
            reasoning_supported=reasoning_supported,
            use_responses_api=use_responses_api,
            model=model,
        )

        if use_responses_api and aresponses is None and self.strict_api_mode:
            return LLMResponse(
                content=(
                    "Responses API is configured (api=openai-responses), but litellm.aresponses is unavailable. "
                    "Install/upgrade litellm to a version that supports Responses API, or set strict_api_mode=false "
                    "to allow fallback to chat completions."
                ),
                finish_reason="error",
                error=True,
                model=model,
            )

        # For OpenRouter, prefix model name if not already prefixed
        if self.is_openrouter and not model.startswith("openrouter/"):
            model = f"openrouter/{model}"
        
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "timeout": self.request_timeout,
        }
        request_headers = self._build_request_headers()
        
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if request_headers:
            kwargs["extra_headers"] = request_headers
        custom_provider = self._resolve_custom_llm_provider()
        if custom_provider:
            kwargs["custom_llm_provider"] = custom_provider
        
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if send_reasoning:
            kwargs["reasoning"] = {"enabled": True}

        if use_responses_api:
            # Responses API typically expects max_output_tokens. Keep max_tokens for
            # compatibility and add max_output_tokens for gateways that require it.
            kwargs["max_output_tokens"] = max_tokens
        
        last_error: Optional[Exception] = None
        
        for attempt in range(self.max_retries + 1):
            try:
                if use_responses_api and aresponses is not None:
                    responses_kwargs: Dict[str, Any] = {
                        "model": model,
                        "input": self._messages_to_responses_input(messages),
                        "max_output_tokens": max_tokens,
                        "timeout": self.request_timeout,
                    }
                    if self.api_base:
                        responses_kwargs["api_base"] = self.api_base
                    if self.api_key:
                        responses_kwargs["api_key"] = self.api_key
                    if request_headers:
                        responses_kwargs["extra_headers"] = request_headers
                    if custom_provider:
                        responses_kwargs["custom_llm_provider"] = custom_provider
                    if tools:
                        responses_kwargs["tools"] = self._tools_to_responses_format(tools)
                        responses_kwargs["tool_choice"] = "auto"
                    if send_reasoning:
                        responses_kwargs["reasoning"] = {"enabled": True}
                    response = await aresponses(**responses_kwargs)
                else:
                    if use_responses_api and aresponses is None:
                        logger.warning(
                            "Responses API requested but litellm.aresponses is unavailable; falling back to chat completions."
                        )
                    response = await acompletion(**kwargs)
                result = self._parse_response(response)
                if attempt > 0:
                    logger.info(
                        "LLM call succeeded after %d retries: model=%s request_id=%s",
                        attempt, result.model, result.request_id,
                    )
                else:
                    logger.info(
                        "LLM call completed: model=%s request_id=%s tokens=%s",
                        result.model, result.request_id, result.usage.get("total_tokens", "?"),
                        extra={"request_id": result.request_id, "model": result.model, "tokens": result.usage},
                    )
                return result
                
            except Exception as e:
                last_error = e
                summarized_error = self._summarize_error(e)
                
                # Check if this error is retryable
                if not self._is_retryable_error(e) or attempt >= self.max_retries:
                    logger.error(
                        "LLM call failed (attempt %d/%d, not retrying): model=%s error=%s",
                        attempt + 1, self.max_retries + 1, model, summarized_error,
                    )
                    break
                
                # Calculate backoff and wait
                delay = self._calculate_backoff(attempt)
                logger.warning(
                    "LLM call failed (attempt %d/%d), retrying in %.1fs: model=%s error=%s",
                    attempt + 1, self.max_retries + 1, delay, model, summarized_error,
                )
                await asyncio.sleep(delay)
        
        # All retries exhausted - return error response
        return LLMResponse(
            content=(
                f"Error calling LLM after {self.max_retries + 1} attempts: "
                f"{self._summarize_error(last_error)}"
            ),
            finish_reason="error",
            error=True,
            model=model,
        )
    
    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse LiteLLM response into our standard format."""
        # Responses API shape (no choices/message envelope)
        if not hasattr(response, "choices"):
            content = getattr(response, "output_text", None) or ""
            tool_calls: List[ToolCallRequest] = []
            output = getattr(response, "output", None)
            if not content:
                text_parts: List[str] = []
                if isinstance(output, list):
                    for item in output:
                        item_type = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
                        if item_type in {"function_call", "tool_call"}:
                            call_id = (
                                getattr(item, "call_id", None)
                                or getattr(item, "id", None)
                                or (item.get("call_id") if isinstance(item, dict) else None)
                                or (item.get("id") if isinstance(item, dict) else None)
                            )
                            name = (
                                getattr(item, "name", None)
                                or (item.get("name") if isinstance(item, dict) else None)
                            )
                            arguments = (
                                getattr(item, "arguments", None)
                                or (item.get("arguments") if isinstance(item, dict) else None)
                            )
                            if isinstance(arguments, str):
                                try:
                                    parsed_arguments = json.loads(arguments)
                                except json.JSONDecodeError:
                                    parsed_arguments = {"raw": arguments}
                            elif isinstance(arguments, dict):
                                parsed_arguments = arguments
                            elif arguments is None:
                                parsed_arguments = {}
                            else:
                                parsed_arguments = {"raw": str(arguments)}
                            if name:
                                tool_calls.append(
                                    ToolCallRequest(
                                        id=str(call_id or f"call_{len(tool_calls) + 1}"),
                                        name=str(name),
                                        arguments=parsed_arguments,
                                    )
                                )
                            continue
                        if item_type != "message":
                            continue
                        item_content = getattr(item, "content", None)
                        if item_content is None and isinstance(item, dict):
                            item_content = item.get("content")
                        if isinstance(item_content, list):
                            for part in item_content:
                                ptype = getattr(part, "type", None) or (part.get("type") if isinstance(part, dict) else None)
                                if ptype in {"output_text", "text"}:
                                    text = getattr(part, "text", None) or (part.get("text") if isinstance(part, dict) else None)
                                    if text:
                                        text_parts.append(str(text))
                content = "".join(text_parts)

            usage = {}
            resp_usage = getattr(response, "usage", None)
            if resp_usage:
                usage = {
                    "prompt_tokens": getattr(resp_usage, "input_tokens", 0),
                    "completion_tokens": getattr(resp_usage, "output_tokens", 0),
                    "total_tokens": getattr(resp_usage, "total_tokens", 0),
                }
            request_id = getattr(response, "request_id", None) or getattr(response, "id", None)
            resp_model = getattr(response, "model", None)
            finish_reason = getattr(response, "status", None) or "stop"
            return LLMResponse(
                content=content,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                usage=usage,
                request_id=request_id,
                model=resp_model,
            )

        choice = response.choices[0]
        message = choice.message
        
        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                # Parse arguments from JSON string if needed
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                
                tool_calls.append(ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))
        
        usage = {}
        if hasattr(response, "usage") and response.usage:
            # Handle object-like usage from LiteLLM
            try:
                usage = {
                    "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(response.usage, "completion_tokens", 0),
                    "total_tokens": getattr(response.usage, "total_tokens", 0),
                }
            except Exception:
                pass
        
        # Extract request ID and model name from response
        # Prefer provider-specific request_id (e.g. Aliyun/DashScope) over
        # the generic completion ID.
        request_id = None
        hidden = getattr(response, "_hidden_params", {}) or {}
        # LiteLLM may expose original response headers
        headers = hidden.get("additional_headers", {}) or hidden.get("headers", {}) or {}
        if headers:
            request_id = (
                headers.get("x-request-id")
                or headers.get("x-dashscope-request-id")
                or headers.get("request-id")
            )
        # Fallback: some providers put request_id directly on the response object
        if not request_id:
            request_id = getattr(response, "request_id", None)
        # Last resort: use the completion ID (chatcmpl-xxx)
        if not request_id:
            request_id = getattr(response, "id", None)
        resp_model = getattr(response, "model", None)

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            request_id=request_id,
            model=resp_model,
        )
    
    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Stream chat completion tokens via LiteLLM.

        Only streams plain text responses (no tool calls).  If the model
        returns tool calls, falls back to the non-streaming path.
        """
        model = model or self.default_model
        use_responses_api = self._should_use_responses_api(model)
        if self.is_openrouter and not model.startswith("openrouter/"):
            model = f"openrouter/{model}"

        if use_responses_api:
            # Keep behavior deterministic for current pipeline: use non-streaming
            # response path and yield the final content once.
            resp = await self.chat(
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            if resp.content:
                yield resp.content
            return

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "timeout": self.request_timeout,
        }
        request_headers = self._build_request_headers()
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if request_headers:
            kwargs["extra_headers"] = request_headers
        custom_provider = self._resolve_custom_llm_provider()
        if custom_provider:
            kwargs["custom_llm_provider"] = custom_provider
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = await acompletion(**kwargs)
            stream_request_id = None
            stream_model = None
            async for chunk in response:
                # Capture request_id and model from the first chunk
                if stream_request_id is None:
                    # Try provider-specific headers first
                    hidden = getattr(chunk, "_hidden_params", {}) or {}
                    hdrs = hidden.get("additional_headers", {}) or hidden.get("headers", {}) or {}
                    stream_request_id = (
                        hdrs.get("x-request-id")
                        or hdrs.get("x-dashscope-request-id")
                        or getattr(chunk, "request_id", None)
                        or getattr(chunk, "id", None)
                    )
                    stream_model = getattr(chunk, "model", None)
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and getattr(delta, "content", None):
                    yield delta.content
            logger.info(
                "LLM stream completed: model=%s request_id=%s",
                stream_model, stream_request_id,
                extra={"request_id": stream_request_id, "model": stream_model},
            )
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield f"\n[Error streaming: {e}]"

    def get_default_model(self) -> str:
        return self.default_model
