"""Tests for tools.registry -- ToolRegistry."""

import asyncio
import pytest
from unittest.mock import patch
from tools.base import Tool
from tools.registry import ToolRegistry


class FakeTool(Tool):
    def __init__(self, name, owner_only=False, provider="core"):
        self._name = name
        self._owner_only = owner_only
        self._provider = provider

    @property
    def name(self):
        return self._name

    @property
    def description(self):
        return f"Fake {self._name}"

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "required": []}

    @property
    def owner_only(self):
        return self._owner_only

    @property
    def provider(self):
        return self._provider

    async def execute(self, **kwargs):
        return f"executed:{self._name}"


@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register(FakeTool("safe_tool", False, provider="system"))
    r.register(FakeTool("std_tool", False, provider="coding"))
    r.register(FakeTool("priv_tool", True, provider="desktop"))
    return r


class TestToolRegistry:
    def test_register_and_len(self, registry):
        assert len(registry) == 3
        assert "safe_tool" in registry

    def test_unregister(self, registry):
        registry.unregister("std_tool")
        assert len(registry) == 2
        assert "std_tool" not in registry

    def test_get(self, registry):
        tool = registry.get("safe_tool")
        assert tool is not None
        assert tool.name == "safe_tool"
        assert registry.get("nonexistent") is None

    def test_has(self, registry):
        assert registry.has("priv_tool") is True
        assert registry.has("missing") is False

    def test_get_definitions(self, registry):
        # Without sender context, PRIVILEGED (owner-only) tools are excluded (fail-closed)
        defs = registry.get_definitions()
        assert len(defs) == 2
        names = [d["function"]["name"] for d in defs]
        assert sorted(names) == ["safe_tool", "std_tool"]
        for d in defs:
            assert d["type"] == "function"

        # With owner sender context, all tools are included
        defs_owner = registry.get_definitions(sender_id="owner", channel="web")
        assert len(defs_owner) == 3

    def test_get_definitions_owner_only_filter(self, registry):
        # Without sender context, owner-only tools are excluded (fail-closed)
        non_owner_defs = registry.get_definitions(
            sender_id="user-1",
            channel="telegram",
        )
        non_owner_names = [item["function"]["name"] for item in non_owner_defs]
        assert "priv_tool" not in non_owner_names
        assert sorted(non_owner_names) == ["safe_tool", "std_tool"]

        owner_defs = registry.get_definitions(
            sender_id="owner",
            channel="web",
        )
        owner_names = [item["function"]["name"] for item in owner_defs]
        assert sorted(owner_names) == ["priv_tool", "safe_tool", "std_tool"]

    def test_denylist(self, registry):
        registry.set_denylist(["priv_tool"])
        defs = registry.get_definitions()
        names = [d["function"]["name"] for d in defs]
        assert "priv_tool" not in names
        assert len(defs) == 2

    def test_allowlist(self, registry):
        registry.set_allowlist(["safe_tool"])
        defs = registry.get_definitions()
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "safe_tool"

    def test_policy_allow_names(self, registry):
        from tools.registry import ToolPolicy

        defs = registry.get_definitions(policy=ToolPolicy(allow_names={"std_tool"}))
        assert [d["function"]["name"] for d in defs] == ["std_tool"]

    def test_policy_deny_names(self, registry):
        from tools.registry import ToolPolicy

        defs = registry.get_definitions(policy=ToolPolicy(deny_names={"std_tool", "priv_tool"}))
        assert [d["function"]["name"] for d in defs] == ["safe_tool"]

    def test_policy_allow_provider(self, registry):
        from tools.registry import ToolPolicy

        defs = registry.get_definitions(policy=ToolPolicy(allow_providers={"coding"}))
        assert [d["function"]["name"] for d in defs] == ["std_tool"]

    def test_policy_deny_provider(self, registry):
        from tools.registry import ToolPolicy

        defs = registry.get_definitions(policy=ToolPolicy(deny_providers={"desktop"}))
        names = [d["function"]["name"] for d in defs]
        assert "priv_tool" not in names
        assert sorted(names) == ["safe_tool", "std_tool"]

    def test_normalize_tool_policy_expands_groups(self):
        from tools.registry import normalize_tool_policy

        policy = normalize_tool_policy(
            {"allow_groups": ["coding"], "deny_groups": ["danger"]},
            groups={"coding": ["std_tool"], "danger": ["priv_tool"]},
        )
        assert policy.allow_names == {"std_tool"}
        assert policy.deny_names == {"priv_tool"}

    @pytest.mark.asyncio
    async def test_execute_success(self, registry):
        result = await registry.execute("safe_tool", {})
        assert result == "executed:safe_tool"

    @pytest.mark.asyncio
    async def test_execute_not_found(self, registry):
        result = await registry.execute("missing", {})
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_execute_blocked_owner_only(self, registry):
        result = await registry.execute("priv_tool", {})
        assert "not permitted" in result

    @pytest.mark.asyncio
    async def test_execute_privileged_restricted_for_non_owner(self, registry):
        result = await registry.execute(
            "priv_tool",
            {},
            sender_id="user-1",
            channel="telegram",
        )
        assert "TOOL_NOT_PERMITTED" in result
        assert "owner channels" in result

    @pytest.mark.asyncio
    async def test_execute_privileged_owner_runs_without_confirmation(self, registry):
        result = await registry.execute(
            "priv_tool",
            {},
            sender_id="owner",
            channel="web",
        )
        assert result == "executed:priv_tool"

    @pytest.mark.asyncio
    async def test_execute_privileged_non_owner_cannot_bypass_with_confirmed(self, registry):
        result = await registry.execute(
            "priv_tool",
            {},
            sender_id="user-1",
            channel="telegram",
        )
        assert "TOOL_NOT_PERMITTED" in result

    @pytest.mark.asyncio
    async def test_execute_owner_only_blocked_without_sender_context(self, registry):
        """C2 regression: owner-only tools MUST be denied when no sender context is provided."""
        result = await registry.execute(
            "priv_tool",
            {},
        )
        assert "TOOL_NOT_PERMITTED" in result

    @pytest.mark.asyncio
    async def test_execute_injects_nested_access_context(self, registry):
        from tools.registry import ToolPolicy

        class CaptureTool(FakeTool):
            def __init__(self):
                super().__init__("capture_tool", False, provider="coding")
                self.last_kwargs = None

            async def execute(self, **kwargs):
                self.last_kwargs = kwargs
                return "ok"

        capture_tool = CaptureTool()
        registry.register(capture_tool)
        policy = ToolPolicy(allow_names={"capture_tool"})

        result = await registry.execute(
            "capture_tool",
            {"payload": "x"},
            policy=policy,
            sender_id="u1",
            channel="web",
        )

        assert result == "ok"
        assert capture_tool.last_kwargs is not None
        assert capture_tool.last_kwargs["payload"] == "x"
        assert capture_tool.last_kwargs["_access_sender_is_owner"] is not None
        assert capture_tool.last_kwargs["_access_policy"] is policy
        assert capture_tool.last_kwargs["_access_sender_id"] == "u1"
        assert capture_tool.last_kwargs["_access_channel"] == "web"

    def test_tool_names(self, registry):
        names = registry.tool_names
        assert sorted(names) == ["priv_tool", "safe_tool", "std_tool"]

    @pytest.mark.asyncio
    @patch("tools.registry.gazer_config")
    async def test_execute_opens_circuit_after_repeated_failures(self, mock_config, registry):
        class FailTool(FakeTool):
            async def execute(self, **kwargs):
                return "Error [X_FAIL]: fail"

        registry.register(FailTool("flaky_tool", False, provider="system"))

        def _get(key, default=None):
            cfg = {
                "security.auto_approve_privileged": False,
                "security.tool_circuit_breaker_enabled": True,
                "security.tool_circuit_breaker_failures": 2,
                "security.tool_circuit_breaker_cooldown_seconds": 60,
            }
            return cfg.get(key, default)

        mock_config.get.side_effect = _get

        first = await registry.execute("flaky_tool", {})
        second = await registry.execute("flaky_tool", {})
        third = await registry.execute("flaky_tool", {})
        assert "X_FAIL" in first
        assert "X_FAIL" in second
        assert "TOOL_CIRCUIT_OPEN" in third

    @pytest.mark.asyncio
    @patch("tools.registry.gazer_config")
    async def test_execute_circuit_resets_after_success(self, mock_config, registry):
        class FlipTool(FakeTool):
            def __init__(self):
                super().__init__("flip_tool", False, provider="system")
                self.calls = 0

            async def execute(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return "Error [X_FAIL]: fail"
                return "ok"

        registry.register(FlipTool())

        def _get(key, default=None):
            cfg = {
                "security.auto_approve_privileged": False,
                "security.tool_circuit_breaker_enabled": True,
                "security.tool_circuit_breaker_failures": 2,
                "security.tool_circuit_breaker_cooldown_seconds": 60,
            }
            return cfg.get(key, default)

        mock_config.get.side_effect = _get

        first = await registry.execute("flip_tool", {})
        second = await registry.execute("flip_tool", {})
        third = await registry.execute("flip_tool", {})
        assert "X_FAIL" in first
        assert second == "ok"
        assert third == "ok"

    @pytest.mark.asyncio
    @patch("tools.registry.gazer_config")
    async def test_execute_budget_blocks_after_limit(self, mock_config, registry):
        def _get(key, default=None):
            cfg = {
                "security.auto_approve_privileged": False,
                "security.tool_circuit_breaker_enabled": False,
                "security.tool_budget_enabled": True,
                "security.tool_budget_window_seconds": 60,
                "security.tool_budget_max_calls": 1,
            }
            return cfg.get(key, default)

        mock_config.get.side_effect = _get

        first = await registry.execute("safe_tool", {})
        second = await registry.execute("safe_tool", {})
        assert first == "executed:safe_tool"
        assert "TOOL_BUDGET_EXCEEDED" in second

    @pytest.mark.asyncio
    @patch("tools.registry.gazer_config")
    async def test_execute_budget_blocks_by_group_cap(self, mock_config, registry):
        def _get(key, default=None):
            cfg = {
                "security.auto_approve_privileged": False,
                "security.tool_circuit_breaker_enabled": False,
                "security.tool_budget_enabled": True,
                "security.tool_budget_window_seconds": 60,
                "security.tool_budget_max_calls": 10,
                "security.tool_budget_max_weight": 10.0,
                "security.tool_budget_max_calls_by_group": {"system": 1},
                "security.tool_budget_weight_by_group": {},
                "security.tool_budget_weight_by_tool": {},
            }
            return cfg.get(key, default)

        mock_config.get.side_effect = _get
        first = await registry.execute("safe_tool", {})
        second = await registry.execute("safe_tool", {})
        assert first == "executed:safe_tool"
        assert "TOOL_BUDGET_EXCEEDED" in second
        assert "group_calls:system" in second

    @pytest.mark.asyncio
    @patch("tools.registry.gazer_config")
    async def test_execute_budget_blocks_by_weight(self, mock_config, registry):
        def _get(key, default=None):
            cfg = {
                "security.auto_approve_privileged": False,
                "security.tool_circuit_breaker_enabled": False,
                "security.tool_budget_enabled": True,
                "security.tool_budget_window_seconds": 60,
                "security.tool_budget_max_calls": 10,
                "security.tool_budget_max_weight": 3.0,
                "security.tool_budget_max_calls_by_group": {},
                "security.tool_budget_weight_by_group": {"coding": 2.0},
                "security.tool_budget_weight_by_tool": {},
            }
            return cfg.get(key, default)

        mock_config.get.side_effect = _get
        first = await registry.execute("std_tool", {})
        second = await registry.execute("std_tool", {})
        assert first == "executed:std_tool"
        assert "TOOL_BUDGET_EXCEEDED" in second
        assert "max_weight" in second

    @pytest.mark.asyncio
    @patch("tools.registry.gazer_config")
    async def test_records_policy_rejection_event(self, mock_config, registry):
        def _get(key, default=None):
            cfg = {
                "security.auto_approve_privileged": False,
                "security.tool_circuit_breaker_enabled": False,
                "security.tool_budget_enabled": False,
            }
            return cfg.get(key, default)

        mock_config.get.side_effect = _get
        # Use std_tool (STANDARD tier) to test tier rejection without
        # interference from owner-only fail-closed (only PRIVILEGED is owner-only)
        result = await registry.execute("priv_tool", {})
        assert "TOOL_NOT_PERMITTED" in result
        events = registry.get_recent_rejection_events(limit=10)
        assert len(events) >= 1
        assert events[0]["code"] == "TOOL_NOT_PERMITTED"
        assert events[0]["tool"] == "priv_tool"
        assert events[0]["reason"] == "blocked_by_owner_only_no_context"

    @pytest.mark.asyncio
    @patch("tools.registry.gazer_config")
    async def test_budget_runtime_status_and_rejection_event(self, mock_config, registry):
        def _get(key, default=None):
            cfg = {
                "security.auto_approve_privileged": False,
                "security.tool_circuit_breaker_enabled": False,
                "security.tool_budget_enabled": True,
                "security.tool_budget_window_seconds": 60,
                "security.tool_budget_max_calls": 1,
                "security.tool_budget_max_weight": 5.0,
                "security.tool_budget_max_calls_by_group": {"system": 2},
                "security.tool_budget_weight_by_group": {},
                "security.tool_budget_weight_by_tool": {},
            }
            return cfg.get(key, default)

        mock_config.get.side_effect = _get
        first = await registry.execute("safe_tool", {})
        assert first == "executed:safe_tool"

        status = registry.get_budget_runtime_status()
        assert status["enabled"] is True
        assert status["used_calls"] == 1
        assert status["remaining_calls"] == 0
        assert status["group_usage"]["system"]["used_calls"] == 1
        assert status["group_usage"]["system"]["cap_calls"] == 2

        second = await registry.execute("safe_tool", {})
        assert "TOOL_BUDGET_EXCEEDED" in second
        events = registry.get_recent_rejection_events(limit=10)
        assert events[0]["code"] == "TOOL_BUDGET_EXCEEDED"
        assert events[0]["reason"] == "max_calls"
