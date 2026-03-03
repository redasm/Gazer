"""AgentLoop mixin: Channel Commands.

Extracted from loop.py to reduce file size.
Contains 27 methods.
"""

from __future__ import annotations

from agent.constants import *  # noqa: F403
from bus.events import InboundMessage
import logging
import time
logger = logging.getLogger('AgentLoop')

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # Add type imports as needed


def _lazy_get_owner_manager():
    from security.owner import get_owner_manager as _fn
    return _fn()

def _lazy_parse_channel_command(content):
    from agent.channel_command_registry import parse_channel_command as _fn
    return _fn(content)

def _lazy_model_registry():
    from soul.models import ModelRegistry as _cls
    return _cls

# Module-level aliases used throughout this mixin
get_owner_manager = _lazy_get_owner_manager
parse_channel_command = _lazy_parse_channel_command


class _ModelRegistryProxy:
    """Proxy that lazily imports ModelRegistry on attribute access."""
    def __getattr__(self, name):
        from soul.models import ModelRegistry as _cls
        return getattr(_cls, name)

ModelRegistry = _ModelRegistryProxy()


class ChannelCommandsMixin:
    """Mixin providing channel commands functionality."""

    @staticmethod
    def _parse_channel_command(content: str) -> Optional[tuple[str, List[str]]]:
        return parse_channel_command(content)

    @staticmethod
    def _command_usage_model() -> str:
        return (
            "用法:\n"
            "- /model\n"
            "- /model show\n"
            "- /model set slow <provider> <model>\n"
            "- /model set fast <provider> <model>\n"
            "说明: 也支持使用 `+` 前缀。"
        )

    @staticmethod
    def _command_usage_router() -> str:
        return (
            "用法:\n"
            "- /router\n"
            "- /router show\n"
            "- /router on\n"
            "- /router off\n"
            "- /router strategy <priority|latency|success_rate>\n"
            "说明: 也支持使用 `+` 前缀。"
        )

    @staticmethod
    def _command_usage_tools() -> str:
        return (
            "用法:\n"
            "- /tools\n"
            "- /tools show\n"
            "说明: 也支持使用 `+` 前缀。"
        )

    @staticmethod
    def _command_usage_policy() -> str:
        return (
            "用法:\n"
            "- /policy\n"
            "- /policy show\n"
            "说明: 也支持使用 `+` 前缀。"
        )

    @staticmethod
    def _command_usage_memory() -> str:
        return (
            "用法:\n"
            "- /memory\n"
            "- /memory show\n"
            "说明: 也支持使用 `+` 前缀。"
        )

    @staticmethod
    def _format_compact_items(items: List[str], *, limit: int = 8) -> str:
        values = [str(item).strip() for item in items if str(item).strip()]
        if not values:
            return "[]"
        preview = values[: max(1, int(limit))]
        suffix = ""
        if len(values) > len(preview):
            suffix = f" ... (+{len(values) - len(preview)})"
        return "[" + ", ".join(preview) + "]" + suffix

    @staticmethod
    def _normalize_sender_id_list(value: Any) -> List[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    def _resolve_command_role(self, msg: InboundMessage) -> str:
        channel = str(msg.channel or "").strip().lower()
        sender_id = str(msg.sender_id or "").strip()
        if channel in TRUSTED_LOCAL_COMMAND_CHANNELS:
            return "owner"
        try:
            owner_mgr = get_owner_manager()
            if owner_mgr and owner_mgr.is_owner_sender(channel, sender_id):
                return "owner"
        except Exception:
            logger.warning("Failed to verify command owner identity.", exc_info=True)

        from runtime.config_manager import config as _cfg

        readonly_cfg = _cfg.get("security.readonly_channel_ids", {}) or {}
        channel_values: List[str] = []
        if isinstance(readonly_cfg, dict):
            channel_values = self._normalize_sender_id_list(readonly_cfg.get(channel))
        elif isinstance(readonly_cfg, (list, tuple, set, str)):
            channel_values = self._normalize_sender_id_list(readonly_cfg)
        if sender_id and ("*" in channel_values or sender_id in channel_values):
            return "readonly"
        return "user"

    @staticmethod
    def _describe_command_role(role: str) -> str:
        clean = str(role or "").strip().lower()
        if clean == "owner":
            return "owner"
        if clean == "readonly":
            return "只读"
        return "普通用户"

    @staticmethod
    def _count_jsonl_rows(path: Path, *, max_scan_bytes: int = 4 * 1024 * 1024) -> str:
        try:
            if not path.is_file():
                return "0"
            size_bytes = int(path.stat().st_size)
            if size_bytes > int(max_scan_bytes):
                return f"文件较大({size_bytes} bytes)，跳过计数"
            rows = 0
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if line.strip():
                        rows += 1
            return str(rows)
        except OSError:
            return "n/a"

    def _build_tools_status_text(self, *, msg: InboundMessage) -> str:
        role = self._resolve_command_role(msg)
        max_tier = self._resolve_tool_max_tier(msg)
        policy = self._resolve_tool_policy()
        tool_defs = self.tools.get_definitions(
            max_tier=max_tier,
            policy=policy,
            sender_id=msg.sender_id,
            channel=msg.channel,
        )
        names = sorted(
            {
                str(((item.get("function", {}) if isinstance(item, dict) else {}) or {}).get("name", "")).strip()
                for item in tool_defs
                if str(((item.get("function", {}) if isinstance(item, dict) else {}) or {}).get("name", "")).strip()
            }
        )
        budget = self.tools.get_budget_runtime_status()
        max_calls, max_parallel = self._get_tool_governance_limits()
        lane_limits = self._get_parallel_tool_lane_limits()
        return (
            "当前工具运行状态:\n"
            f"- role={self._describe_command_role(role)}\n"
            f"- 可用工具数={len(names)}\n"
            f"- 可用工具预览={self._format_compact_items(names, limit=10)}\n"
            f"- max_tool_calls_per_turn={max_calls}\n"
            f"- max_parallel_tool_calls={max_parallel}\n"
            f"- lane_limits={lane_limits}\n"
            f"- budget.enabled={bool(budget.get('enabled', False))}\n"
            f"- budget.used_calls={int(budget.get('used_calls', 0))}/{int(budget.get('max_calls', 0))}\n"
            f"- budget.used_weight={float(budget.get('used_weight', 0.0))}/{float(budget.get('max_weight', 0.0))}"
        )

    def _build_policy_status_text(self, *, msg: InboundMessage) -> str:
        role = self._resolve_command_role(msg)
        tier = self._resolve_tool_max_tier(msg)
        policy = self._resolve_tool_policy()
        pipeline_status = self.get_tool_policy_pipeline_status()
        pipeline_steps = pipeline_status.get("steps", []) if isinstance(pipeline_status, dict) else []
        pipeline_preview = []
        if isinstance(pipeline_steps, list):
            for step in pipeline_steps:
                if not isinstance(step, dict):
                    continue
                label = str(step.get("label", "")).strip()
                if not label:
                    continue
                marker = "applied" if bool(step.get("applied", False)) else "skip"
                if bool(step.get("changed", False)):
                    marker = f"{marker}*"
                pipeline_preview.append(f"{label}:{marker}")
        release_gate = self._eval_benchmark_manager.get_release_gate_status()
        gate_blocked = bool((release_gate or {}).get("blocked", False))
        gate_reason = str((release_gate or {}).get("reason", "")).strip()
        return (
            "当前策略状态:\n"
            f"- role={self._describe_command_role(role)}\n"
            f"- effective_max_tier={tier.value}\n"
            f"- allow_names={self._format_compact_items(sorted(policy.allow_names), limit=8)}\n"
            f"- deny_names={self._format_compact_items(sorted(policy.deny_names), limit=8)}\n"
            f"- allow_providers={self._format_compact_items(sorted(policy.allow_providers), limit=8)}\n"
            f"- deny_providers={self._format_compact_items(sorted(policy.deny_providers), limit=8)}\n"
            f"- allow_model_providers={self._format_compact_items(sorted(policy.allow_model_providers), limit=8)}\n"
            f"- deny_model_providers={self._format_compact_items(sorted(policy.deny_model_providers), limit=8)}\n"
            f"- allow_model_names={self._format_compact_items(sorted(policy.allow_model_names), limit=8)}\n"
            f"- deny_model_names={self._format_compact_items(sorted(policy.deny_model_names), limit=8)}\n"
            f"- policy_pipeline={self._format_compact_items(pipeline_preview, limit=6)}\n"
            f"- tool_call_hooks.blocked_loop_calls={int(self._tool_call_hooks.get_status().get('blocked_loop_calls', 0))}\n"
            f"- release_gate.blocked={gate_blocked}\n"
            f"- release_gate.reason={gate_reason or 'n/a'}"
        )

    def _build_memory_status_text(self) -> str:
        from runtime.config_manager import config as _cfg
        from memory.openviking_bootstrap import load_openviking_settings

        settings = load_openviking_settings(_cfg)
        events_path = settings.data_dir / "memory_events.jsonl"
        store_path = settings.data_dir / "store"
        event_rows = self._count_jsonl_rows(events_path)
        store_exists = store_path.is_dir()
        return (
            "当前记忆后端状态:\n"
            f"- backend.enabled={bool(settings.enabled)}\n"
            f"- backend.mode={settings.mode}\n"
            f"- data_dir={settings.data_dir}\n"
            f"- config_file={settings.config_file or 'n/a'}\n"
            f"- session_prefix={settings.session_prefix}\n"
            f"- default_user={settings.default_user}\n"
            f"- commit_every_messages={int(settings.commit_every_messages)}\n"
            f"- memory_events.path={events_path}\n"
            f"- memory_events.rows={event_rows}\n"
            f"- store_dir.exists={store_exists}"
        )

    def _build_channel_command_help(self) -> str:
        return (
            "可用命令:\n"
            "- /help\n"
            "- /new 或 /reset\n"
            "- /model\n"
            "- /model set slow <provider> <model>\n"
            "- /model set fast <provider> <model>\n"
            "- /router\n"
            "- /router on|off\n"
            "- /router strategy <priority|latency|success_rate>\n"
            "- /tools\n"
            "- /policy\n"
            "- /memory\n"
            "权限分层:\n"
            "- owner: 可执行全部命令\n"
            "- 普通用户: 可执行查询命令和 /new /reset\n"
            "- 只读: 仅可执行查询命令\n"
            "说明: 也支持使用 `+` 前缀。"
        )

    def _build_model_status_text(self) -> str:
        from runtime.config_manager import config as _cfg

        model_defaults = _cfg.get("agents.defaults.model", {}) or {}
        primary_ref = ""
        fallback_ref = ""
        if isinstance(model_defaults, str):
            primary_ref = str(model_defaults).strip()
        elif isinstance(model_defaults, dict):
            primary_ref = str(model_defaults.get("primary", "") or "").strip()
            fallbacks = model_defaults.get("fallbacks", [])
            if isinstance(fallbacks, list) and fallbacks:
                fallback_ref = str(fallbacks[0] or "").strip()
        if not fallback_ref:
            fallback_ref = primary_ref

        slow_provider, slow_model = ModelRegistry.resolve_model_ref("slow_brain")
        fast_provider, fast_model = ModelRegistry.resolve_model_ref("fast_brain")
        runtime_slow_model = str(self._active_model_override or self.model or "")
        runtime_fast_model = str(self._fast_model or "(disabled)")
        return (
            "当前模型配置:\n"
            f"- primary(slow_brain): ref={primary_ref or 'n/a'} provider={slow_provider or 'n/a'} model={slow_model or 'n/a'}\n"
            f"- fallback0(fast_brain): ref={fallback_ref or 'n/a'} provider={fast_provider or 'n/a'} model={fast_model or 'n/a'}\n"
            "当前运行时:\n"
            f"- slow_model={runtime_slow_model or 'n/a'}\n"
            f"- fast_model={runtime_fast_model}"
        )

    def _build_router_status_text(self) -> str:
        from runtime.config_manager import config as _cfg

        router = _cfg.get("models.router", {}) or {}
        rollout = router.get("rollout", {}) if isinstance(router, dict) else {}
        if not isinstance(rollout, dict):
            rollout = {}
        channels_raw = rollout.get("channels", [])
        channels = (
            [str(item).strip() for item in channels_raw if str(item).strip()]
            if isinstance(channels_raw, list)
            else []
        )
        return (
            "当前 Router 配置:\n"
            f"- enabled={bool(router.get('enabled', False))}\n"
            f"- strategy={str(router.get('strategy', 'priority') or 'priority')}\n"
            f"- strategy_template={str(router.get('strategy_template', 'custom') or 'custom')}\n"
            f"- rollout.enabled={bool(rollout.get('enabled', False))}\n"
            f"- rollout.owner_only={bool(rollout.get('owner_only', False))}\n"
            f"- rollout.channels={channels if channels else '[]'}"
        )

    def _is_command_authorized(self, msg: InboundMessage, *, mutating: bool) -> bool:
        if not mutating:
            return True
        return self._resolve_command_role(msg) == "owner"

    def _register_channel_command_handlers(self) -> None:
        def _router_mutating(args: List[str]) -> bool:
            action = str(args[0]).strip().lower() if args else ""
            return action in {"on", "off", "enable", "disable", "degrade", "strategy", "set"}

        def _model_mutating(args: List[str]) -> bool:
            return bool(args) and str(args[0]).strip().lower() == "set"

        self.channel_command_registry.register("help", self._handle_command_help, aliases=["h"])
        self.channel_command_registry.register(
            "new",
            self._handle_command_reset,
            aliases=["reset"],
            mutating=True,
        )
        self.channel_command_registry.register("tools", self._handle_command_tools, aliases=["t"])
        self.channel_command_registry.register("policy", self._handle_command_policy, aliases=["p"])
        self.channel_command_registry.register("memory", self._handle_command_memory, aliases=["mem"])
        self.channel_command_registry.register(
            "router",
            self._handle_command_router,
            aliases=["r"],
            mutating=_router_mutating,
        )
        self.channel_command_registry.register(
            "model",
            self._handle_command_model,
            aliases=["m"],
            mutating=_model_mutating,
        )

    def _handle_command_help(self, args: List[str], msg: InboundMessage) -> str:
        del args, msg
        return self._build_channel_command_help()

    def _handle_command_reset(self, args: List[str], msg: InboundMessage) -> str:
        del args
        self.reset_session(msg.session_key)
        return "会话已重置。"

    def _handle_command_tools(self, args: List[str], msg: InboundMessage) -> str:
        if not args or str(args[0]).strip().lower() in {"show", "status", "list"}:
            return self._build_tools_status_text(msg=msg)
        return self._command_usage_tools()

    def _handle_command_policy(self, args: List[str], msg: InboundMessage) -> str:
        if not args or str(args[0]).strip().lower() in {"show", "status", "list"}:
            return self._build_policy_status_text(msg=msg)
        return self._command_usage_policy()

    def _handle_command_memory(self, args: List[str], msg: InboundMessage) -> str:
        del msg
        if not args or str(args[0]).strip().lower() in {"show", "status", "list"}:
            try:
                return self._build_memory_status_text()
            except Exception as exc:
                logger.error("Failed to build memory status text: %s", exc, exc_info=True)
                return f"读取 memory 状态失败: {exc}"
        return self._command_usage_memory()

    def _handle_command_router(self, args: List[str], msg: InboundMessage) -> str:
        if not args or str(args[0]).strip().lower() in {"show", "status", "list"}:
            return self._build_router_status_text()

        action = str(args[0]).strip().lower()
        role_label = self._describe_command_role(self._resolve_command_role(msg))
        if not self._is_command_authorized(msg, mutating=True):
            return (
                f"权限不足: 当前角色={role_label}。该命令仅 owner 或本地 web 渠道可执行。\n"
                "请在 `security.owner_channel_ids` 配置 owner 的渠道 ID。"
            )

        from runtime.config_manager import config as _cfg

        if action in {"off", "disable", "degrade"}:
            try:
                _cfg.set_many(
                    {
                        "models.router.enabled": False,
                        "models.router.rollout.enabled": False,
                    }
                )
            except Exception as exc:
                logger.error("Failed to disable router: %s", exc, exc_info=True)
                return f"Router 降级失败: {exc}"
            return "已执行一键降级：router 已关闭（models.router.enabled=false）。"

        if action in {"on", "enable"}:
            try:
                _cfg.set_many(
                    {
                        "models.router.enabled": True,
                        "models.router.rollout.enabled": True,
                        "models.router.rollout.owner_only": True,
                    }
                )
            except Exception as exc:
                logger.error("Failed to enable router rollout: %s", exc, exc_info=True)
                return f"Router 启用失败: {exc}"
            return "router 已启用，并切换为灰度模式（owner_only=true）。"

        if action in {"strategy", "set"}:
            if len(args) < 2:
                return self._command_usage_router()
            strategy = str(args[1]).strip().lower()
            if strategy not in {"priority", "latency", "success_rate"}:
                return "参数错误: strategy 只能是 priority|latency|success_rate。"
            try:
                _cfg.set_many(
                    {
                        "models.router.strategy": strategy,
                        "models.router.strategy_template": "custom",
                    }
                )
            except Exception as exc:
                logger.error("Failed to update router strategy: %s", exc, exc_info=True)
                return f"Router 策略更新失败: {exc}"
            return f"router 策略已更新为 {strategy}。"

        return self._command_usage_router()

    def _handle_command_model(self, args: List[str], msg: InboundMessage) -> str:
        if not args or str(args[0]).strip().lower() in {"show", "status", "list"}:
            return self._build_model_status_text()

        action = str(args[0]).strip().lower()
        if action != "set":
            return self._command_usage_model()
        if len(args) < 4:
            return self._command_usage_model()

        role = str(args[1]).strip().lower()
        role_alias = {
            "slow": "slow_brain",
            "slow_brain": "slow_brain",
            "fast": "fast_brain",
            "fast_brain": "fast_brain",
        }
        brain_key = role_alias.get(role)
        if brain_key is None:
            return "参数错误: role 只能是 slow|fast。"

        provider = str(args[2]).strip()
        model = " ".join([str(item) for item in args[3:]]).strip()
        if not provider or not model:
            return self._command_usage_model()

        role_label = self._describe_command_role(self._resolve_command_role(msg))
        if not self._is_command_authorized(msg, mutating=True):
            return (
                f"权限不足: 当前角色={role_label}。该命令仅 owner 或本地 web 渠道可执行。\n"
                "请在 `security.owner_channel_ids` 配置 owner 的渠道 ID。"
            )

        from runtime.config_manager import config as _cfg

        current_provider, _ = ModelRegistry.resolve_model_ref(brain_key)
        model_ref = f"{provider}/{model}"
        updates = (
            {"agents.defaults.model.primary": model_ref}
            if brain_key == "slow_brain"
            else {"agents.defaults.model.fallbacks": [model_ref]}
        )
        try:
            _cfg.set_many(updates)
        except Exception as exc:
            logger.error("Failed to persist model profile update: %s", exc, exc_info=True)
            return f"模型配置更新失败: {exc}"

        immediate = False
        if brain_key == "slow_brain" and current_provider == provider:
            self.model = model
            immediate = True
        if brain_key == "fast_brain" and current_provider == provider and self._fast_provider is not None:
            self._fast_model = model
            immediate = True

        if immediate:
            return (
                f"已更新 {brain_key}: provider={provider}, model={model}\n"
                "变更已立即生效。"
            )
        return (
            f"已更新 {brain_key}: provider={provider}, model={model}\n"
            "配置已保存；若切换了 provider，需重启后完全生效。"
        )

    def _execute_channel_command(self, *, command: str, args: List[str], msg: InboundMessage) -> str:
        cmd = str(command or "").strip().lower()
        role = self._resolve_command_role(msg)

        if role == "readonly" and self.channel_command_registry.is_mutating(cmd, args):
            return "权限不足: 当前角色=只读。只读用户不允许执行写操作命令。"

        result = self.channel_command_registry.execute(command=cmd, args=args, context=msg)
        if result is not None:
            return result
        return (
            f"未知命令: `{cmd}`\n"
            "发送 `/help` 查看可用命令。"
        )

