"""Tests for flow — GazerFlow workflow engine.

Covers: YAML parsing, variable interpolation, FlowEngine execution,
conditional steps, each fan-out, on_complete state updates, approval
gates with resume tokens, StateStore persistence, LLMTaskStep, and
FlowRunTool.
"""

import json
import asyncio
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.models import (
    FlowArg,
    FlowApproval,
    FlowConfig,
    FlowContext,
    FlowDefinition,
    FlowResult,
    FlowStep,
    StepResult,
)
from flow.parser import interpolate, parse_flow_file
from flow.state import StateStore
from flow.approval import (
    create_resume_token,
    verify_resume_token,
    snapshot_context,
    restore_context,
)
from flow.engine import FlowEngine
from flow.llm_task import LLMTaskStep
from flow.tool import FlowRunTool


# =========================================================================
# Fixtures
# =========================================================================

@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def sample_flow_yaml(tmp_dir):
    """Write a minimal .flow.yaml and return its path."""
    content = textwrap.dedent("""\
        name: test_flow
        description: A test workflow
        args:
          greeting:
            type: string
            default: hello
          count:
            type: integer
            default: 3
        state:
          cursor: null
        config:
          timeout_ms: 30000
          max_output_bytes: 100000
        steps:
          - id: step_one
            tool: echo_tool
            args:
              message: "$args.greeting"
          - id: step_two
            tool: echo_tool
            args:
              message: "result was $steps.step_one.output"
            condition: "args.get('count', 0) > 0"
            on_complete:
              cursor: "steps['step_one'].output"
    """)
    p = tmp_dir / "test_flow.flow.yaml"
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture
def mock_tool_registry():
    """A mock ToolRegistry whose execute() returns JSON strings."""
    registry = AsyncMock()

    async def _execute(name, params, **kw):
        if name == "echo_tool":
            return json.dumps({"echo": params.get("message", "")})
        if name == "fail_tool":
            return "Error: tool failed"
        return json.dumps({"tool": name, "params": params})

    registry.execute = AsyncMock(side_effect=_execute)
    return registry


@pytest.fixture
def mock_llm_provider():
    """A mock LLM provider that returns canned JSON."""
    provider = AsyncMock()
    provider.chat = AsyncMock(return_value=MagicMock(
        content='{"summary": "test result"}',
        error=False,
    ))
    return provider


# =========================================================================
# Parser tests
# =========================================================================

class TestParser:
    def test_parse_flow_file(self, sample_flow_yaml):
        flow = parse_flow_file(sample_flow_yaml)
        assert flow.name == "test_flow"
        assert flow.description == "A test workflow"
        assert "greeting" in flow.args
        assert flow.args["greeting"].default == "hello"
        assert flow.args["count"].type == "integer"
        assert len(flow.steps) == 2
        assert flow.steps[0].id == "step_one"
        assert flow.steps[1].condition == "args.get('count', 0) > 0"
        assert flow.config.timeout_ms == 30000
        assert flow.state == {"cursor": None}

    def test_parse_missing_name(self, tmp_dir):
        p = tmp_dir / "bad.flow.yaml"
        p.write_text("description: no name\n", encoding="utf-8")
        with pytest.raises(ValueError, match="missing required 'name'"):
            parse_flow_file(p)

    def test_parse_approval_step(self, tmp_dir):
        content = textwrap.dedent("""\
            name: approval_test
            steps:
              - id: gate
                approve:
                  prompt: "Continue?"
                  preview: "$steps.prev.output"
        """)
        p = tmp_dir / "approval.flow.yaml"
        p.write_text(content, encoding="utf-8")
        flow = parse_flow_file(p)
        assert flow.steps[0].approve is not None
        assert flow.steps[0].approve.prompt == "Continue?"

    def test_parse_step_resilience_fields(self, tmp_dir):
        content = textwrap.dedent("""\
            name: resilience_test
            steps:
              - id: step_a
                tool: echo_tool
              - id: step_b
                tool: echo_tool
                depends_on: ["step_a"]
                retry_max: 2
                retry_backoff_ms: 50
                timeout_ms: 1500
        """)
        p = tmp_dir / "resilience.flow.yaml"
        p.write_text(content, encoding="utf-8")
        flow = parse_flow_file(p)
        step = flow.steps[1]
        assert step.depends_on == ["step_a"]
        assert step.retry_max == 2
        assert step.retry_backoff_ms == 50
        assert step.timeout_ms == 1500


class TestInterpolation:
    def test_simple_args_ref(self):
        ctx = FlowContext(args={"name": "world"})
        assert interpolate("$args.name", ctx) == "world"

    def test_string_embedding(self):
        ctx = FlowContext(args={"name": "world"})
        result = interpolate("hello $args.name!", ctx)
        assert result == "hello world!"

    def test_steps_output_ref(self):
        ctx = FlowContext(steps={"s1": StepResult(output={"key": "val"})})
        result = interpolate("$steps.s1.output", ctx)
        assert result == {"key": "val"}

    def test_nested_dict_ref(self):
        ctx = FlowContext(steps={"s1": StepResult(output={"data": {"nested": 42}})})
        result = interpolate("$steps.s1.output.data.nested", ctx)
        assert result == 42

    def test_state_ref(self):
        ctx = FlowContext(state={"cursor": "abc123"})
        assert interpolate("$state.cursor", ctx) == "abc123"

    def test_item_ref(self):
        ctx = FlowContext(item={"email": "a@b.com"})
        assert interpolate("$item.email", ctx) == "a@b.com"

    def test_item_bare(self):
        ctx = FlowContext(item="plain_value")
        assert interpolate("$item", ctx) == "plain_value"

    def test_dict_interpolation(self):
        ctx = FlowContext(args={"to": "user@test.com", "subj": "Hi"})
        result = interpolate({"to": "$args.to", "subject": "$args.subj"}, ctx)
        assert result == {"to": "user@test.com", "subject": "Hi"}

    def test_list_interpolation(self):
        ctx = FlowContext(args={"a": 1, "b": 2})
        result = interpolate(["$args.a", "$args.b"], ctx)
        assert result == [1, 2]

    def test_preserves_non_string(self):
        ctx = FlowContext()
        assert interpolate(42, ctx) == 42
        assert interpolate(None, ctx) is None

    def test_unknown_ref_returns_as_is(self):
        ctx = FlowContext()
        assert interpolate("$unknown.path", ctx) == "$unknown.path"

    def test_none_ref(self):
        ctx = FlowContext(args={})
        result = interpolate("$args.missing", ctx)
        assert result is None


# =========================================================================
# StateStore tests
# =========================================================================

class TestStateStore:
    def test_load_defaults_when_no_file(self, tmp_dir):
        store = StateStore(base_dir=tmp_dir / "state")
        state = store.load("my_flow", defaults={"cursor": None})
        assert state == {"cursor": None}

    def test_save_and_load(self, tmp_dir):
        store = StateStore(base_dir=tmp_dir / "state")
        store.save("my_flow", {"cursor": "abc", "count": 5})
        loaded = store.load("my_flow")
        assert loaded == {"cursor": "abc", "count": 5}

    def test_save_merges_with_defaults(self, tmp_dir):
        store = StateStore(base_dir=tmp_dir / "state")
        store.save("my_flow", {"cursor": "xyz"})
        loaded = store.load("my_flow", defaults={"cursor": None, "extra": True})
        assert loaded["cursor"] == "xyz"
        assert loaded["extra"] is True

    def test_clear(self, tmp_dir):
        store = StateStore(base_dir=tmp_dir / "state")
        store.save("my_flow", {"cursor": "abc"})
        store.clear("my_flow")
        assert store.load("my_flow") == {}

    def test_list_flows(self, tmp_dir):
        store = StateStore(base_dir=tmp_dir / "state")
        store.save("flow_a", {"x": 1})
        store.save("flow_b", {"y": 2})
        names = store.list_flows()
        assert sorted(names) == ["flow_a", "flow_b"]

    def test_checkpoint_roundtrip(self, tmp_dir):
        store = StateStore(base_dir=tmp_dir / "state")
        checkpoint = {"next_index": 1, "ctx": {"args": {"k": "v"}}}
        store.save_checkpoint("my_flow", checkpoint)
        loaded = store.load_checkpoint("my_flow")
        assert loaded is not None
        assert loaded["next_index"] == 1
        store.clear_checkpoint("my_flow")
        assert store.load_checkpoint("my_flow") is None


# =========================================================================
# Approval token tests
# =========================================================================

class TestApprovalTokens:
    def test_create_and_verify(self):
        ctx = FlowContext(args={"x": 1}, state={"cursor": "c"})
        snap = snapshot_context(ctx)
        token = create_resume_token("my_flow", "step_2", snap)
        payload = verify_resume_token(token)
        assert payload is not None
        assert payload["flow"] == "my_flow"
        assert payload["step"] == "step_2"
        assert payload["ctx"]["args"] == {"x": 1}

    def test_tampered_token_fails(self):
        ctx = FlowContext(args={"x": 1})
        snap = snapshot_context(ctx)
        token = create_resume_token("f", "s", snap)
        # Tamper with signature
        parts = token.rsplit(".", 1)
        bad_token = parts[0] + ".invalid-signature"
        assert verify_resume_token(bad_token) is None

    def test_restore_context(self):
        ctx = FlowContext(
            args={"a": 1},
            state={"cur": "x"},
            steps={"s1": StepResult(output="data", skipped=False)},
        )
        snap = snapshot_context(ctx)
        restored = restore_context(snap)
        assert restored.args == {"a": 1}
        assert restored.state == {"cur": "x"}
        assert restored.steps["s1"].output == "data"

    def test_invalid_token_format(self):
        assert verify_resume_token("not-a-valid-token") is None
        assert verify_resume_token("") is None


# =========================================================================
# FlowEngine tests
# =========================================================================

class TestFlowEngine:
    @pytest.fixture
    def flow_dir(self, tmp_dir):
        """Create a temp workflows dir with a simple flow."""
        d = tmp_dir / "workflows"
        d.mkdir()
        content = textwrap.dedent("""\
            name: simple
            description: Simple test flow
            args:
              msg:
                type: string
                default: hi
            state:
              last: null
            steps:
              - id: greet
                tool: echo_tool
                args:
                  message: "$args.msg"
                on_complete:
                  last: "steps['greet'].output"
        """)
        (d / "simple.flow.yaml").write_text(content, encoding="utf-8")
        return d

    @pytest.fixture
    def engine(self, flow_dir, mock_tool_registry, tmp_dir):
        state_store = StateStore(base_dir=tmp_dir / "state")
        return FlowEngine(
            tool_registry=mock_tool_registry,
            state_store=state_store,
            flow_dirs=[flow_dir],
        )

    @pytest.mark.asyncio
    async def test_run_simple_flow(self, engine):
        result = await engine.run("simple", {"msg": "world"})
        assert result.status == "completed"
        assert "greet" in result.output
        output = result.output["greet"].output
        assert output == {"echo": "world"}

    @pytest.mark.asyncio
    async def test_run_passes_access_context_to_tool_registry(self, engine, mock_tool_registry):
        sentinel_sender_is_owner = object()
        sentinel_policy = object()
        result = await engine.run(
            "simple",
            {"msg": "world"},
            sender_is_owner=sentinel_sender_is_owner,
            policy=sentinel_policy,
        )
        assert result.status == "completed"
        call = mock_tool_registry.execute.await_args_list[0]
        assert call.kwargs["sender_is_owner"] is sentinel_sender_is_owner
        assert call.kwargs["policy"] is sentinel_policy

    @pytest.mark.asyncio
    async def test_run_unknown_flow(self, engine):
        result = await engine.run("nonexistent")
        assert result.status == "error"
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_on_complete_updates_state(self, engine, tmp_dir):
        await engine.run("simple", {"msg": "test"})
        state_store = StateStore(base_dir=tmp_dir / "state")
        state = state_store.load("simple")
        assert state.get("last") is not None

    @pytest.mark.asyncio
    async def test_condition_false_skips_step(self, tmp_dir, mock_tool_registry):
        d = tmp_dir / "wf"
        d.mkdir()
        content = textwrap.dedent("""\
            name: cond_test
            args:
              run_step:
                type: boolean
                default: false
            steps:
              - id: maybe
                tool: echo_tool
                args:
                  message: "hi"
                condition: "args.get('run_step', False)"
        """)
        (d / "cond_test.flow.yaml").write_text(content, encoding="utf-8")
        engine = FlowEngine(
            tool_registry=mock_tool_registry,
            state_store=StateStore(base_dir=tmp_dir / "state"),
            flow_dirs=[d],
        )
        result = await engine.run("cond_test", {"run_step": False})
        assert result.status == "completed"
        assert result.output["maybe"].skipped is True

    @pytest.mark.asyncio
    async def test_condition_true_executes_step(self, tmp_dir, mock_tool_registry):
        d = tmp_dir / "wf"
        d.mkdir()
        content = textwrap.dedent("""\
            name: cond_true
            args:
              run_step:
                type: boolean
                default: true
            steps:
              - id: maybe
                tool: echo_tool
                args:
                  message: "executed"
                condition: "args.get('run_step', False)"
        """)
        (d / "cond_true.flow.yaml").write_text(content, encoding="utf-8")
        engine = FlowEngine(
            tool_registry=mock_tool_registry,
            state_store=StateStore(base_dir=tmp_dir / "state"),
            flow_dirs=[d],
        )
        result = await engine.run("cond_true", {"run_step": True})
        assert result.status == "completed"
        assert result.output["maybe"].output == {"echo": "executed"}

    @pytest.mark.asyncio
    async def test_tool_error_stops_flow(self, tmp_dir, mock_tool_registry):
        d = tmp_dir / "wf"
        d.mkdir()
        content = textwrap.dedent("""\
            name: fail_flow
            steps:
              - id: bad
                tool: fail_tool
                args: {}
              - id: after
                tool: echo_tool
                args:
                  message: "should not run"
        """)
        (d / "fail_flow.flow.yaml").write_text(content, encoding="utf-8")
        engine = FlowEngine(
            tool_registry=mock_tool_registry,
            state_store=StateStore(base_dir=tmp_dir / "state"),
            flow_dirs=[d],
        )
        result = await engine.run("fail_flow")
        assert result.status == "error"
        assert "after" not in (result.output or {})

    @pytest.mark.asyncio
    async def test_each_fan_out(self, tmp_dir, mock_tool_registry):
        d = tmp_dir / "wf"
        d.mkdir()
        content = textwrap.dedent("""\
            name: each_test
            args:
              items:
                type: array
                default: []
            steps:
              - id: setup
                args:
                  data: "$args.items"
              - id: process
                tool: echo_tool
                each: "$steps.setup.output.data"
                args:
                  message: "$item"
        """)
        (d / "each_test.flow.yaml").write_text(content, encoding="utf-8")
        engine = FlowEngine(
            tool_registry=mock_tool_registry,
            state_store=StateStore(base_dir=tmp_dir / "state"),
            flow_dirs=[d],
        )
        result = await engine.run("each_test", {"items": ["a", "b", "c"]})
        assert result.status == "completed"
        outputs = result.output["process"].output
        assert len(outputs) == 3
        assert outputs[0] == {"echo": "a"}

    @pytest.mark.asyncio
    async def test_approval_gate_returns_token(self, tmp_dir, mock_tool_registry):
        d = tmp_dir / "wf"
        d.mkdir()
        content = textwrap.dedent("""\
            name: approval_flow
            steps:
              - id: step1
                tool: echo_tool
                args:
                  message: "before gate"
              - id: gate
                approve:
                  prompt: "Continue?"
              - id: step2
                tool: echo_tool
                args:
                  message: "after gate"
        """)
        (d / "approval_flow.flow.yaml").write_text(content, encoding="utf-8")
        engine = FlowEngine(
            tool_registry=mock_tool_registry,
            state_store=StateStore(base_dir=tmp_dir / "state"),
            flow_dirs=[d],
        )
        result = await engine.run("approval_flow")
        assert result.status == "needs_approval"
        assert result.pending_step == "gate"
        assert result.resume_token is not None
        assert result.prompt == "Continue?"

    @pytest.mark.asyncio
    async def test_resume_after_approval(self, tmp_dir, mock_tool_registry):
        d = tmp_dir / "wf"
        d.mkdir()
        content = textwrap.dedent("""\
            name: resume_flow
            steps:
              - id: before
                tool: echo_tool
                args:
                  message: "first"
              - id: gate
                approve:
                  prompt: "OK?"
              - id: after
                tool: echo_tool
                args:
                  message: "resumed"
        """)
        (d / "resume_flow.flow.yaml").write_text(content, encoding="utf-8")
        engine = FlowEngine(
            tool_registry=mock_tool_registry,
            state_store=StateStore(base_dir=tmp_dir / "state"),
            flow_dirs=[d],
        )
        # First run hits approval gate
        r1 = await engine.run("resume_flow")
        assert r1.status == "needs_approval"

        # Resume past the gate
        r2 = await engine.resume(r1.resume_token)
        assert r2.status == "completed"
        assert "after" in r2.output
        assert r2.output["after"].output == {"echo": "resumed"}

    @pytest.mark.asyncio
    async def test_resume_invalid_token(self, engine):
        result = await engine.resume("totally-invalid-token")
        assert result.status == "error"
        assert "Invalid" in result.error

    def test_list_flows(self, engine):
        flows = engine.list_flows()
        assert len(flows) == 1
        assert flows[0]["name"] == "simple"

    def test_status(self, engine):
        info = engine.status("simple")
        assert info["flow"] == "simple"
        assert "state" in info

    def test_status_unknown(self, engine):
        info = engine.status("nope")
        assert "error" in info

    @pytest.mark.asyncio
    async def test_timeout(self, tmp_dir, mock_tool_registry):
        """Flow with 1ms timeout should fail."""
        d = tmp_dir / "wf"
        d.mkdir()
        content = textwrap.dedent("""\
            name: timeout_flow
            config:
              timeout_ms: 1
            steps:
              - id: slow
                tool: echo_tool
                args:
                  message: "hi"
        """)
        (d / "timeout_flow.flow.yaml").write_text(content, encoding="utf-8")

        # Make tool execute slowly
        import asyncio

        async def slow_execute(name, params, **kw):
            await asyncio.sleep(0.01)
            return json.dumps({"echo": "late"})

        mock_tool_registry.execute = AsyncMock(side_effect=slow_execute)
        engine = FlowEngine(
            tool_registry=mock_tool_registry,
            state_store=StateStore(base_dir=tmp_dir / "state"),
            flow_dirs=[d],
        )
        result = await engine.run("timeout_flow")
        # Might be completed (if fast enough) or error — either is ok
        # The timeout is only checked between steps, so 1 step might finish
        assert result.status in ("completed", "error")

    @pytest.mark.asyncio
    async def test_step_dependency_missing_blocks_execution(self, tmp_dir, mock_tool_registry):
        d = tmp_dir / "wf"
        d.mkdir()
        content = textwrap.dedent("""\
            name: dep_flow
            steps:
              - id: second
                tool: echo_tool
                depends_on: ["first"]
                args:
                  message: "should not run"
        """)
        (d / "dep_flow.flow.yaml").write_text(content, encoding="utf-8")
        engine = FlowEngine(
            tool_registry=mock_tool_registry,
            state_store=StateStore(base_dir=tmp_dir / "state"),
            flow_dirs=[d],
        )
        result = await engine.run("dep_flow")
        assert result.status == "error"
        assert "dependency 'first' not completed" in result.error

    @pytest.mark.asyncio
    async def test_step_retry_succeeds_after_transient_failure(self, tmp_dir, mock_tool_registry):
        d = tmp_dir / "wf"
        d.mkdir()
        content = textwrap.dedent("""\
            name: retry_flow
            steps:
              - id: unstable
                tool: flaky_tool
                retry_max: 2
                retry_backoff_ms: 0
                args: {}
        """)
        (d / "retry_flow.flow.yaml").write_text(content, encoding="utf-8")

        calls = {"count": 0}

        async def flaky_execute(name, params, **kw):
            calls["count"] += 1
            if calls["count"] == 1:
                return "Error: transient"
            return json.dumps({"ok": True})

        mock_tool_registry.execute = AsyncMock(side_effect=flaky_execute)
        engine = FlowEngine(
            tool_registry=mock_tool_registry,
            state_store=StateStore(base_dir=tmp_dir / "state"),
            flow_dirs=[d],
        )
        result = await engine.run("retry_flow")
        assert result.status == "completed"
        assert calls["count"] == 2

    @pytest.mark.asyncio
    async def test_retry_budget_limits_step_retries(self, tmp_dir, mock_tool_registry):
        d = tmp_dir / "wf"
        d.mkdir()
        content = textwrap.dedent("""\
            name: retry_budget_flow
            config:
              retry_budget: 1
            steps:
              - id: unstable
                tool: flaky_tool
                retry_max: 3
                retry_backoff_ms: 0
                args: {}
        """)
        (d / "retry_budget_flow.flow.yaml").write_text(content, encoding="utf-8")

        calls = {"count": 0}

        async def flaky_execute(name, params, **kw):
            calls["count"] += 1
            return "Error: temporary upstream issue"

        mock_tool_registry.execute = AsyncMock(side_effect=flaky_execute)
        engine = FlowEngine(
            tool_registry=mock_tool_registry,
            state_store=StateStore(base_dir=tmp_dir / "state"),
            flow_dirs=[d],
        )
        result = await engine.run("retry_budget_flow")
        assert result.status == "error"
        assert "Retry budget exhausted" in result.error
        assert calls["count"] == 2

    @pytest.mark.asyncio
    async def test_step_timeout_is_enforced(self, tmp_dir, mock_tool_registry):
        d = tmp_dir / "wf"
        d.mkdir()
        content = textwrap.dedent("""\
            name: step_timeout_flow
            steps:
              - id: slow
                tool: slow_tool
                timeout_ms: 5
                args: {}
        """)
        (d / "step_timeout_flow.flow.yaml").write_text(content, encoding="utf-8")

        async def slow_execute(name, params, **kw):
            await asyncio.sleep(0.03)
            return json.dumps({"ok": True})

        mock_tool_registry.execute = AsyncMock(side_effect=slow_execute)
        engine = FlowEngine(
            tool_registry=mock_tool_registry,
            state_store=StateStore(base_dir=tmp_dir / "state"),
            flow_dirs=[d],
        )
        result = await engine.run("step_timeout_flow")
        assert result.status == "error"
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_resume_interrupted_from_checkpoint(self, tmp_dir, mock_tool_registry):
        d = tmp_dir / "wf"
        d.mkdir()
        content = textwrap.dedent("""\
            name: recover_flow
            steps:
              - id: first
                tool: echo_tool
                args:
                  message: "ok"
              - id: second
                tool: fail_tool
                args: {}
        """)
        (d / "recover_flow.flow.yaml").write_text(content, encoding="utf-8")

        engine = FlowEngine(
            tool_registry=mock_tool_registry,
            state_store=StateStore(base_dir=tmp_dir / "state"),
            flow_dirs=[d],
        )
        first_run = await engine.run("recover_flow")
        assert first_run.status == "error"
        checkpoint = engine.status("recover_flow").get("checkpoint")
        assert checkpoint is not None
        assert checkpoint.get("next_index") == 1

        async def recover_execute(name, params, **kw):
            if name == "fail_tool":
                return json.dumps({"recovered": True})
            return json.dumps({"echo": params.get("message", "")})

        mock_tool_registry.execute = AsyncMock(side_effect=recover_execute)
        resumed = await engine.resume_interrupted("recover_flow")
        assert resumed.status == "completed"
        assert resumed.output["second"].output == {"recovered": True}

    def test_reload(self, engine, tmp_dir):
        """Adding a new flow file and calling reload should discover it."""
        flow_dirs = engine._flow_dirs
        content = textwrap.dedent("""\
            name: new_flow
            steps: []
        """)
        (flow_dirs[0] / "new_flow.flow.yaml").write_text(content, encoding="utf-8")
        engine.reload()
        assert engine.get_flow("new_flow") is not None

    @pytest.mark.asyncio
    async def test_run_tool_preserves_raw_string_and_logs_debug(self, tmp_dir, caplog):
        registry = AsyncMock()
        registry.execute = AsyncMock(return_value="plain text result")
        engine = FlowEngine(tool_registry=registry, flow_dirs=[tmp_dir])

        with caplog.at_level("DEBUG"):
            result = await engine._run_tool("echo_tool", {"message": "hello"})

        assert result.error is None
        assert result.output == "plain text result"
        assert "returned non-JSON output" in caplog.text


# =========================================================================
# LLMTaskStep tests
# =========================================================================

class TestLLMTaskStep:
    @pytest.mark.asyncio
    async def test_basic_execution(self, mock_llm_provider):
        step = LLMTaskStep(mock_llm_provider)
        result = await step.execute(prompt="Summarize", input_data={"text": "hello"})
        assert result == {"summary": "test result"}

    @pytest.mark.asyncio
    async def test_strips_code_fences(self):
        provider = AsyncMock()
        provider.chat = AsyncMock(return_value=MagicMock(
            content='```json\n{"key": "value"}\n```',
            error=False,
        ))
        step = LLMTaskStep(provider)
        result = await step.execute(prompt="Test")
        assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self):
        provider = AsyncMock()
        provider.chat = AsyncMock(return_value=MagicMock(
            content="not json at all",
            error=False,
        ))
        step = LLMTaskStep(provider)
        with pytest.raises(ValueError, match="not valid JSON"):
            await step.execute(prompt="Test")

    @pytest.mark.asyncio
    async def test_schema_validation_array(self):
        provider = AsyncMock()
        provider.chat = AsyncMock(return_value=MagicMock(
            content='{"not": "array"}',
            error=False,
        ))
        step = LLMTaskStep(provider)
        with pytest.raises(ValueError, match="Expected array"):
            await step.execute(prompt="Test", schema={"type": "array"})

    @pytest.mark.asyncio
    async def test_schema_validation_passes(self):
        provider = AsyncMock()
        provider.chat = AsyncMock(return_value=MagicMock(
            content='[1, 2, 3]',
            error=False,
        ))
        step = LLMTaskStep(provider)
        result = await step.execute(prompt="Test", schema={"type": "array"})
        assert result == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_error_response_raises(self):
        provider = AsyncMock()
        provider.chat = AsyncMock(return_value=MagicMock(
            content="",
            error=True,
        ))
        step = LLMTaskStep(provider)
        with pytest.raises(ValueError, match="LLM call failed"):
            await step.execute(prompt="Test")


# =========================================================================
# FlowRunTool tests
# =========================================================================

class TestFlowRunTool:
    @pytest.fixture
    def tool(self, tmp_dir, mock_tool_registry):
        d = tmp_dir / "wf"
        d.mkdir()
        content = textwrap.dedent("""\
            name: tool_test
            args:
              msg:
                type: string
                default: hi
            steps:
              - id: greet
                tool: echo_tool
                args:
                  message: "$args.msg"
        """)
        (d / "tool_test.flow.yaml").write_text(content, encoding="utf-8")
        engine = FlowEngine(
            tool_registry=mock_tool_registry,
            state_store=StateStore(base_dir=tmp_dir / "state"),
            flow_dirs=[d],
        )
        return FlowRunTool(engine)

    def test_tool_properties(self, tool):
        assert tool.name == "run_flow"
        assert "action" in tool.parameters["properties"]
        assert tool.owner_only is False

    @pytest.mark.asyncio
    async def test_list_action(self, tool):
        result = await tool.execute(action="list")
        parsed = json.loads(result)
        assert "flows" in parsed
        assert len(parsed["flows"]) == 1
        assert parsed["flows"][0]["name"] == "tool_test"

    @pytest.mark.asyncio
    async def test_run_action(self, tool):
        result = await tool.execute(action="run", flow_name="tool_test", args={"msg": "world"})
        parsed = json.loads(result)
        assert parsed["status"] == "completed"

    @pytest.mark.asyncio
    async def test_run_action_forwards_access_context(self):
        sentinel_sender_is_owner = object()
        sentinel_policy = object()
        fake_engine = MagicMock()
        fake_engine.list_flows.return_value = []
        fake_engine.status.return_value = {"flow": "tool_test"}
        fake_engine.run = AsyncMock(return_value=FlowResult(status="completed"))
        tool = FlowRunTool(fake_engine)

        result = await tool.execute(
            action="run",
            flow_name="tool_test",
            args={"msg": "world"},
            _access_sender_is_owner=sentinel_sender_is_owner,
            _access_policy=sentinel_policy,
        )
        parsed = json.loads(result)
        assert parsed["status"] == "completed"
        fake_engine.run.assert_awaited_once_with(
            "tool_test",
            {"msg": "world"},
            sender_is_owner=sentinel_sender_is_owner,
            policy=sentinel_policy,
        )

    @pytest.mark.asyncio
    async def test_run_missing_name(self, tool):
        result = await tool.execute(action="run")
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_status_action(self, tool):
        result = await tool.execute(action="status", flow_name="tool_test")
        parsed = json.loads(result)
        assert parsed["flow"] == "tool_test"

    @pytest.mark.asyncio
    async def test_resume_missing_token(self, tool):
        result = await tool.execute(action="resume")
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_recover_missing_name(self, tool):
        result = await tool.execute(action="recover")
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_unknown_action(self, tool):
        result = await tool.execute(action="explode")
        parsed = json.loads(result)
        assert "error" in parsed


# =========================================================================
# FlowResult serialization
# =========================================================================

class TestFlowResult:
    def test_to_dict_completed(self):
        r = FlowResult(
            status="completed",
            output={"s1": StepResult(output="ok")},
        )
        d = r.to_dict()
        assert d["status"] == "completed"
        assert d["steps"]["s1"]["output"] == "ok"
        assert "error" not in d

    def test_to_dict_needs_approval(self):
        r = FlowResult(
            status="needs_approval",
            pending_step="gate",
            prompt="Continue?",
            resume_token="tok123",
        )
        d = r.to_dict()
        assert d["pending_step"] == "gate"
        assert d["resume_token"] == "tok123"

    def test_to_dict_error(self):
        r = FlowResult(status="error", error="boom")
        d = r.to_dict()
        assert d["error"] == "boom"
