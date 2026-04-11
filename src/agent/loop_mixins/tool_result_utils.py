"""AgentLoop mixin: Tool Result Utils.

Extracted from loop.py to reduce file size.
Contains 21 methods.
"""

from __future__ import annotations

from agent.constants import *  # noqa: F403
import hashlib
import json
import logging
import uuid
from typing import Any, Dict, List
from tools.media_marker import MEDIA_MARKER
from bus.events import OutboundMessage
logger = logging.getLogger('AgentLoop')

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # Add type imports as needed


class ToolResultUtilsMixin:
    """Mixin providing tool result utils functionality."""

    @staticmethod
    def _resolve_outbound_reply_to(msg: InboundMessage) -> str | None:
        metadata = msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {}
        generic_reply_to = str(metadata.get("reply_to", "") or "").strip()
        if generic_reply_to:
            return generic_reply_to
        if str(getattr(msg, "channel", "") or "").strip().lower() == "feishu":
            feishu_message_id = str(metadata.get("feishu_message_id", "") or "").strip()
            if feishu_message_id:
                return feishu_message_id
        return None

    @staticmethod
    def _strip_media_markers(text: str) -> str:
        if not text or MEDIA_MARKER not in text:
            return (text or "").strip()
        cleaned_parts: List[str] = []
        for idx, segment in enumerate(text.split(MEDIA_MARKER)):
            if idx == 0:
                if segment.strip():
                    cleaned_parts.append(segment.strip())
                continue
            tail = segment.split(maxsplit=1)
            if len(tail) > 1 and tail[1].strip():
                cleaned_parts.append(tail[1].strip())
        return "\n".join(cleaned_parts).strip()

    @staticmethod
    def _build_inbound_metadata_note(metadata: Dict[str, Any]) -> str:
        """Build a compact system note from channel-provided inbound metadata."""
        if not isinstance(metadata, dict) or not metadata:
            return ""
        message_type = str(metadata.get("feishu_message_type", "") or "").strip()
        message_id = str(metadata.get("feishu_message_id", "") or "").strip()
        media_items = metadata.get("feishu_media", [])
        if not isinstance(media_items, list) or not media_items:
            return ""

        lines = [
            "## Inbound Media Context",
            f"source=feishu message_type={message_type or 'unknown'} message_id={message_id or 'unknown'}",
            "media:",
        ]
        for item in media_items[:5]:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("message_type", "") or "").strip() or "unknown"
            item_path = str(item.get("path", "") or "").strip() or "<missing>"
            lines.append(f"- type={item_type} path={item_path}")
        return "\n".join(lines)

    @staticmethod
    def _detect_user_language(text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return _LANG_DEFAULT
        if _CJK_RE.search(raw):
            return "zh"
        if any("a" <= ch.lower() <= "z" for ch in raw):
            return "en"
        return _LANG_DEFAULT

    @staticmethod
    def _msg(lang: str, key: str, **kwargs: Any) -> str:
        pack = _LANG_MESSAGES.get(lang, _LANG_MESSAGES[_LANG_DEFAULT])
        template = str(pack.get(key, _LANG_MESSAGES[_LANG_DEFAULT].get(key, "")))
        if kwargs:
            try:
                return template.format(**kwargs)
            except Exception:
                return template
        return template

    @classmethod
    def _build_tool_call_limit_message(
        cls,
        *,
        lang: str,
        limit: int,
        executed: int,
        requested: int,
    ) -> str:
        return cls._msg(
            lang,
            "tool_call_limit",
            limit=int(limit),
            executed=int(executed),
            requested=int(requested),
        )

    @staticmethod
    def _extract_media(tool_result: str) -> List[str]:
        """Extract media file paths from a tool result string.

        Tools can embed ``__MEDIA__:/path/to/file`` markers in their
        output.  These are collected by the agent loop and attached to
        the final ``OutboundMessage`` so that channels can send the
        files to the user.
        """
        paths: list[str] = []
        for part in tool_result.split(MEDIA_MARKER)[1:]:
            path = part.split()[0].strip() if part.strip() else ""
            if path:
                paths.append(path)
        return paths

    @staticmethod
    def _extract_error_code(tool_result: str) -> str:
        """Parse standardized tool error code from `Error [CODE]: ...`."""
        text = str(tool_result or "").strip()
        if not text.startswith("Error ["):
            return ""
        end = text.find("]:")
        if end <= len("Error ["):
            return ""
        return text[len("Error ["):end].strip()

    @staticmethod
    def _extract_trace_id(tool_result: str) -> str:
        text = str(tool_result or "")
        first = text.splitlines()[0] if text else ""
        marker = "(trace_id="
        if marker not in first:
            return ""
        start = first.find(marker)
        if start < 0:
            return ""
        start += len(marker)
        end = first.find(")", start)
        if end < 0:
            end = len(first)
        return first[start:end].strip()

    @staticmethod
    def _extract_error_hint(tool_result: str) -> str:
        for line in str(tool_result or "").splitlines():
            if line.startswith("Hint:"):
                return line[len("Hint:"):].strip()
        return ""

    def _build_tool_failure_recovery_template(
        self,
        *,
        tool_name: str,
        retryable: bool,
        budget_remaining: int,
    ) -> str:
        lane = self._classify_tool_parallel_lane(tool_name)
        if retryable and int(budget_remaining) > 0:
            retry_action = f"缩小参数范围后重试 1 次（剩余重试预算={int(budget_remaining)}）。"
        elif retryable:
            retry_action = "已无重试预算，不要继续重复同一调用。"
        else:
            retry_action = "该错误通常不可重试，先修正参数/权限后再尝试。"

        if lane == "device":
            alternative_action = "改用 `node_describe` 或仅截图观察，避免直接执行设备动作。"
        elif lane == "network":
            alternative_action = "改用 `web_search` / `web_fetch` 并缩小查询范围。"
        elif lane == "io":
            alternative_action = "改用 `read_file` / `list_dir` 分步收集信息后再执行。"
        else:
            alternative_action = "改用同能力的低风险工具，避免重复当前失败调用。"

        return (
            "Recovery Template:\n"
            f"1) 重试: {retry_action}\n"
            f"2) 替代工具: {alternative_action}\n"
            "3) 降级答复: 若仍失败，明确说明限制，并给出可手动执行的下一步。"
        )

    @staticmethod
    def _new_trace_id() -> str:
        return f"trc_{uuid.uuid4().hex[:12]}"

    @classmethod
    def _build_replan_hint(cls, *, tool_name: str, tool_result: str) -> str:
        """Build an explicit replan hint when a tool call fails."""
        text = str(tool_result or "").strip()
        if not text.startswith("Error"):
            return ""
        code = cls._extract_error_code(text)
        if not code:
            return ""
        guidance = _REPLAN_ERROR_HINTS.get(
            code,
            "Previous tool attempt failed. Replan and avoid repeating the same failure.",
        )
        return (
            f"Tool `{tool_name}` failed with `{code}`. "
            f"{guidance} "
            "If no safe tool path exists, explain the limitation and provide next-step instructions."
        )

    @staticmethod
    def _serialize_tool_arguments(arguments: Any) -> str:
        """Best-effort stable serialization for argument preview/hash."""
        try:
            if isinstance(arguments, str):
                parsed = json.loads(arguments.strip() or "{}")
                return json.dumps(parsed, ensure_ascii=False, sort_keys=True, default=str)
            return json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            return str(arguments)

    @classmethod
    def _build_tool_call_payload(cls, tool_call: Any) -> Dict[str, Any]:
        raw_args = cls._serialize_tool_arguments(getattr(tool_call, "arguments", {}))
        args_hash = hashlib.sha256(raw_args.encode("utf-8", errors="replace")).hexdigest()[:16]
        return {
            "tool": str(getattr(tool_call, "name", "") or ""),
            "tool_call_id": str(getattr(tool_call, "id", "") or ""),
            "args_preview": raw_args[:240],
            "args_hash": args_hash,
        }

    @classmethod
    def _build_tool_result_payload(cls, tool_call: Any, result: str) -> Dict[str, Any]:
        text = str(result or "")
        status = "error" if text.startswith("Error") else "ok"
        media_paths = cls._extract_media(text)
        payload: Dict[str, Any] = {
            "tool": str(getattr(tool_call, "name", "") or ""),
            "tool_call_id": str(getattr(tool_call, "id", "") or ""),
            "status": status,
            "result_preview": text[:240],
            "has_media": bool(media_paths),
            "media_paths": media_paths[:10],
        }
        if status == "error":
            payload["error_code"] = cls._extract_error_code(text)
            trace_id = cls._extract_trace_id(text)
            if trace_id:
                payload["trace_id"] = trace_id
            hint = cls._extract_error_hint(text)
            if hint:
                payload["error_hint"] = hint[:240]
        return payload

    async def _emit_tool_call_stream_event(
        self,
        *,
        channel: str,
        chat_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        """Emit tool-call lifecycle events on the partial outbound stream."""
        if str(channel or "").strip().lower() != "web":
            return
        safe_payload = dict(payload or {})
        try:
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content="",
                    is_partial=True,
                    metadata={
                        "stream_event": "tool_call",
                        "event_type": str(event_type or ""),
                        "payload": safe_payload,
                    },
                )
            )
        except Exception:
            logger.debug("Failed to publish tool-call stream event", exc_info=True)

    @classmethod
    def _tool_failure_fingerprint(cls, tool_call: Any, tool_result: str) -> str:
        raw_args = cls._serialize_tool_arguments(getattr(tool_call, "arguments", {}))
        args_hash = hashlib.sha256(raw_args.encode("utf-8", errors="replace")).hexdigest()[:16]
        code = cls._extract_error_code(tool_result) or "UNKNOWN"
        tool_name = str(getattr(tool_call, "name", "") or "").strip() or "unknown"
        return f"{tool_name}:{args_hash}:{code}"

    @staticmethod
    def _should_abort_on_repeat(error_code: str) -> bool:
        # If arguments are invalid, the model can often fix and retry safely.
        return error_code not in {"TOOL_PARAMS_INVALID", "TOOL_ARGS_INVALID"}

    @classmethod
    def _format_recent_tool_failures(cls, *, lang: str, failures: List[Dict[str, Any]]) -> str:
        if not failures:
            return ""
        lines: List[str] = []
        if lang == "en":
            lines.append("Recent tool failures:")
        else:
            lines.append("本轮最近的工具失败：")
        for item in failures[-3:]:
            tool = str(item.get("tool", "") or "")
            code = str(item.get("error_code", "") or "")
            trace_id = str(item.get("trace_id", "") or "")
            preview = str(item.get("result_preview", "") or "")
            hint = str(item.get("error_hint", "") or "")
            seg = f"- tool={tool} code={code}"
            if trace_id:
                seg += f" trace_id={trace_id}"
            if preview:
                seg += f" message={preview}"
            lines.append(seg[:900])
            if hint:
                lines.append(f"- hint={hint}"[:900])
        return "\n".join(lines).strip()

    @classmethod
    def _build_tool_repeat_abort_message(cls, *, lang: str, failures: List[Dict[str, Any]]) -> str:
        diag = cls._format_recent_tool_failures(lang=lang, failures=failures)
        if lang == "en":
            head = "Tool calls kept failing with the same error; stopping retries."
            tail = "Next: check logs using the trace_id above, then adjust config/dependencies and retry."
        else:
            head = "工具调用反复失败（同一错误重复出现），已停止重试。"
            tail = "下一步：用上面的 trace_id 在日志里定位原因，修复配置/依赖/权限后再重试。"
        if diag:
            return f"{head}\n{diag}\n{tail}".strip()
        return f"{head}\n{tail}".strip()

    @staticmethod
    def _is_fake_tool_call(content: str) -> bool:
        """Detect if LLM is claiming to have performed an action without calling tools.

        Some models output text like "(正在调用工具：screenshot)" or "Screenshot captured"
        without actually making a tool call. This detects such hallucinations.
        """
        if not content:
            return False
        content_lower = content.lower()
        # Patterns that suggest the LLM is faking tool execution
        fake_markers = [
            "正在调用工具", "调用工具", "已捕获", "已截图", "截图已发送", "已发送截图",
            "screenshot captured", "screenshot sent", "captured and sent",
            "(calling tool", "(executing tool", "i have taken a screenshot",
            "i've taken a screenshot", "here is the screenshot",
        ]
        return any(marker in content_lower for marker in fake_markers)
